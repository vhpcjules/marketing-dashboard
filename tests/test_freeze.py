"""The freeze mechanism, proven against the REAL drift case found 2026-09-04."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import pytest
from src.freeze import SnapshotStore, detect_drift, FreezeError, query_hash, REPO_ROOT

SEP5 = date(2026, 9, 5)
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path)


def _seed(store, period, domain, body, frozen):
    store.write_open(period, domain, body, query_id="q", query_hash_="abc", row_count=1,
                     pulled_at=NOW, source="test")
    if frozen:
        store.promote(period, domain, as_of=SEP5, promoted_by="test", note="seed")


class TestStoreGuards:
    def test_live_pull_cannot_overwrite_frozen(self, store):
        _seed(store, "2026-06", "cohorts_m1", {"m1_net_revenue": 130063}, frozen=True)
        with pytest.raises(FreezeError, match="FROZEN"):
            store.write_open("2026-06", "cohorts_m1", {"m1_net_revenue": 125590.58},
                             query_id="q", query_hash_="abc", row_count=1, pulled_at=NOW, source="t")

    def test_live_pull_may_overwrite_open(self, store):
        _seed(store, "2026-08", "cohorts_m1", {"m1_net_revenue": 85123.40}, frozen=False)
        store.write_open("2026-08", "cohorts_m1", {"m1_net_revenue": 86000},
                         query_id="q", query_hash_="abc", row_count=1, pulled_at=NOW, source="t")
        assert store.read("2026-08", "cohorts_m1").metric("m1_net_revenue") == D("86000")

    def test_promote_refuses_open_calendar_month(self, store):
        _seed(store, "2026-09", "marketing_spend", {"postings": {}}, frozen=False)
        with pytest.raises(FreezeError, match="calendar month not closed"):
            store.promote("2026-09", "marketing_spend", as_of=SEP5, promoted_by="t")

    def test_promote_refuses_open_m13_window(self, store):
        _seed(store, "2026-06", "cohorts_m13", {"m13_net_revenue": 1}, frozen=False)
        with pytest.raises(FreezeError, match="90-day window has not closed"):
            store.promote("2026-06", "cohorts_m13", as_of=SEP5, promoted_by="t")

    def test_promote_allows_closed_m13_window(self, store):
        _seed(store, "2026-05", "cohorts_m13", {"m13_net_revenue": 1}, frozen=False)
        store.promote("2026-05", "cohorts_m13", as_of=SEP5, promoted_by="t")
        assert store.read("2026-05", "cohorts_m13").frozen

    def test_promote_is_idempotent_refusal(self, store):
        _seed(store, "2026-07", "marketing_spend", {"postings": {}}, frozen=True)
        with pytest.raises(FreezeError, match="already frozen"):
            store.promote("2026-07", "marketing_spend", as_of=SEP5, promoted_by="t")

    def test_amend_requires_a_real_reason(self, store):
        _seed(store, "2026-06", "cohorts_m1", {"m1_net_revenue": 130063}, frozen=True)
        with pytest.raises(FreezeError, match="reason"):
            store.amend("2026-06", "cohorts_m1", {"m1_net_revenue": 125590.58},
                        as_of=SEP5, amended_by="t", reason="fix")

    def test_amend_keeps_the_old_value_inside_the_file(self, store):
        _seed(store, "2026-06", "cohorts_m1", {"m1_net_revenue": 130063}, frozen=True)
        store.amend("2026-06", "cohorts_m1", {"m1_net_revenue": 125590.58}, as_of=SEP5,
                    amended_by="jules", reason="late credit memos confirmed by accounting 2026-09-xx")
        snap = store.read("2026-06", "cohorts_m1")
        assert snap.metric("m1_net_revenue") == D("125590.58")
        assert snap.meta["amendments"][0]["previous"]["m1_net_revenue"] == 130063

    def test_files_are_diff_friendly(self, store):
        _seed(store, "2026-01", "x", {"b": 2, "a": 1}, frozen=False)
        text = store.path("2026-01", "x").read_text()
        assert text.index('"a"') < text.index('"b"')       # sorted keys
        assert text.endswith("\n") and "\n  " in text        # stable indent, trailing newline


class TestDriftOnTheRealCase:
    """The five moved months from 2026-09-04, against their frozen values."""

    FROZEN = {"2025-03": 90201, "2025-05": 77932, "2025-06": 71999, "2026-06": 130063, "2026-07": 51088,
              "2026-01": 37328}
    LIVE = {"2025-03": 88906.28, "2025-05": 74095.90, "2025-06": 71662.43, "2026-06": 125590.58,
            "2026-07": 49740.92, "2026-01": 37328.43, "2026-08": 85123.40}

    @pytest.fixture
    def seeded(self, store):
        for m, v in self.FROZEN.items():
            _seed(store, m, "cohorts_m1", {"m1_net_revenue": v}, frozen=True)
        _seed(store, "2026-08", "cohorts_m1", {"m1_net_revenue": 80000}, frozen=False)
        return store

    def test_detects_every_moved_frozen_month(self, seeded):
        live = {m: {"m1_net_revenue": v} for m, v in self.LIVE.items()}
        rep = detect_drift(seeded, "cohorts_m1", live, ["m1_net_revenue"], as_of=SEP5)
        moved = {f.period for f in rep.findings}
        assert moved == {"2025-03", "2025-05", "2025-06", "2026-06", "2026-07", "2026-01"}

    def test_breaches_are_the_material_ones(self, seeded):
        live = {m: {"m1_net_revenue": v} for m, v in self.LIVE.items()}
        rep = detect_drift(seeded, "cohorts_m1", live, ["m1_net_revenue"], as_of=SEP5)
        breached = {f.period for f in rep.breaches}
        # 2025-06 moved -0.47% and 2026-01 moved 43 cents: below 1%, findings but not breaches
        assert breached == {"2025-03", "2025-05", "2026-06", "2026-07"}
        assert not rep.ok

    def test_open_period_is_ignored(self, seeded):
        live = {"2026-08": {"m1_net_revenue": 85123.40}}
        rep = detect_drift(seeded, "cohorts_m1", live, ["m1_net_revenue"], as_of=SEP5)
        assert rep.findings == [] and rep.ok

    def test_drift_never_writes_the_snapshot(self, seeded):
        live = {"2026-06": {"m1_net_revenue": 125590.58}}
        detect_drift(seeded, "cohorts_m1", live, ["m1_net_revenue"], as_of=SEP5)
        assert seeded.read("2026-06", "cohorts_m1").metric("m1_net_revenue") == D("130063")

    def test_report_names_metric_period_old_new_magnitude(self, seeded, tmp_path):
        live = {"2026-06": {"m1_net_revenue": 125590.58}}
        rep = detect_drift(seeded, "cohorts_m1", live, ["m1_net_revenue"], as_of=SEP5)
        text = rep.console()
        for needle in ("cohorts_m1", "2026-06", "m1_net_revenue", "130,063.00", "125,590.58", "-3.44%", "BREACH"):
            assert needle in text, needle
        p = rep.write(tmp_path)
        assert p.name == "restatement_2026-09-05.md"
        assert "**No snapshot was changed.**" in p.read_text()

    def test_zero_baseline_is_always_a_breach(self, store):
        _seed(store, "2026-01", "x", {"n": 0}, frozen=True)
        rep = detect_drift(store, "x", {"2026-01": {"n": 5}}, ["n"], as_of=SEP5)
        assert rep.breaches and rep.breaches[0].delta_pct is None


class TestRepoSnapshots:
    """The committed store, as it stands."""

    def test_spend_jan_jul_frozen_aug_open(self):
        s = SnapshotStore()
        assert s.frozen_periods("marketing_spend") == [f"2026-0{i}" for i in range(1, 8)]
        assert not s.read("2026-08", "marketing_spend").frozen

    def test_cohorts_frozen_through_july_at_published_values(self):
        s = SnapshotStore()
        assert s.read("2026-06", "cohorts_m1").frozen
        assert s.read("2026-06", "cohorts_m1").metric("m1_net_revenue") == D("130063")
        assert s.read("2026-06", "cohorts_m1").body["customers"] == 67
        assert not s.read("2026-08", "cohorts_m1").frozen

    def test_frozen_snapshots_record_where_live_disagrees(self):
        snap = SnapshotStore().read("2026-06", "cohorts_m1")
        assert abs(D(str(snap.body["live_at_last_pull"]["m1_net_revenue"])) - D("125590.58")) < D("0.01")


def test_query_hash_is_stable_and_short():
    assert query_hash("SELECT 1") == query_hash("SELECT 1") and len(query_hash("x")) == 16
