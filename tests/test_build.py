"""The build orchestrator against the committed repository.

These tests run the real build on the real data in data/, writing to a
temporary dist/ and reports/ so the repo's own reports/ is untouched. Where
a sibling layer is deliberately absent (render, gate) the build must still
complete, say so, and write the change log - the change log is how a human
reviews a refresh before anything is published.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

import src.build as b
from src.data.spend import SpendData
from src.freeze import SnapshotStore
from src.units import delta

SEP5 = date(2026, 9, 5)
quiet = lambda s: None  # noqa: E731


@pytest.fixture
def out(tmp_path):
    return {"dist": tmp_path / "dist", "reports": tmp_path / "reports"}


class TestBuildWithSiblingsAbsent:
    def test_completes_without_rendering_and_writes_the_change_log(self, out, monkeypatch):
        monkeypatch.setattr(b, "_import_siblings", lambda log: b.Siblings(notes=["render layer absent (test)"]))
        res = b.build(SEP5, out["dist"], reports_dir=out["reports"], log=quiet)
        assert res.reporting_month == "2026-08"
        assert res.rendered == [] and "executive" in res.skipped
        assert res.gate_ok is None                       # no gate to run
        assert res.change_log.exists() and res.change_log.name == "change_log_2026-08.md"
        assert not res.ok                                # nothing rendered is not a deployable build
        text = res.change_log.read_text()
        for needle in ("New customers (M1 basis)", "Month-one NET revenue", "Marketing spend (true operating)",
                       "Phone capture rate", "| Jul 26 | Aug 26 |"):
            assert needle in text, needle

    def test_dist_carries_assets_and_the_public_redirects(self, out, monkeypatch):
        monkeypatch.setattr(b, "_import_siblings", lambda log: b.Siblings())
        b.build(SEP5, out["dist"], reports_dir=out["reports"], log=quiet)
        assert (out["dist"] / "assets" / "css" / "brand.css").exists()
        assert (out["dist"] / "assets" / "vendor" / "chart.umd.min.js").exists()
        redirects = (out["dist"] / "_redirects").read_text()
        assert "/Leadership_Dashboard.html" in redirects, "public/ is copied verbatim, not replaced"
        assert "noindex" in (out["dist"] / "_headers").read_text()


class TestBuildWithSiblingsPresent:
    def test_executive_is_skipped_for_missing_prior_year_spend_not_faked(self, out):
        """No 2025 monthly spend snapshots exist. The page must be skipped with
        the missing IDs named - never rendered with a gap or a guess."""
        res = b.build(SEP5, out["dist"], reports_dir=out["reports"], log=quiet)
        assert res.change_log.exists()
        if "executive" in res.skipped:
            why = res.skipped["executive"]
            assert "ytd25.spend" in why and "2025" in why
            assert not (out["dist"] / "executive" / "index.html").exists()
        else:  # a 2025 spend ingest has landed since this test was written
            assert (out["dist"] / "executive" / "index.html").exists()

    def test_drift_is_reported_and_all_current_breaches_are_acknowledged(self, out):
        res = b.build(SEP5, out["dist"], reports_dir=out["reports"], log=quiet)
        assert res.drift is not None and res.drift.findings
        breached = {f.period for f in res.drift.breaches}
        assert {"2025-03", "2025-05", "2026-06", "2026-07"} <= breached     # the 2026-09-04 findings
        assert res.new_drift_breaches == [], "every current breach is recorded in the frozen file itself"
        assert res.restatement_report and res.restatement_report.exists()
        assert "**No snapshot was changed.**" in res.restatement_report.read_text()

    def test_render_path_end_to_end_with_a_prior_year_spend_fixture(self, out, monkeypatch):
        """Real 2026 data plus a synthetic 2025 spend object (a TEST FIXTURE,
        labelled as such, never written to data/) so the whole page renders
        and the gate inspects it. Every traceability failure is ours to fix."""
        real = b._load_spend

        def fake(year):
            got = real(year)
            if got is not None or year != 2025:
                return got          # the repo now carries 2025 monthly snapshots; the fixture is a fallback only
            postings = {f"2025-{m:02d}": {"66212.0016": D("30000"), "66212.0017": D("10000")} for m in range(1, 13)}
            return SpendData(year=2025, postings=postings, corrections=[], budget={"accounts": {}},
                             _meta={"months": {m: {"frozen": True} for m in postings}, "budget": {}})
        monkeypatch.setattr(b, "_load_spend", fake)
        res = b.build(SEP5, out["dist"], reports_dir=out["reports"], log=quiet)
        assert res.skipped == {}, res.skipped
        pages = {p.parent.name: p for p in res.rendered}
        assert set(pages) == {"executive", "marketing-ops", "sales"}
        html = pages["executive"].read_text()
        for mid in ("aug26.new_customers", "ytd26.roas_m1", "ytd26.roas_to_date", "fy26.target",
                    "ytd25.spend", "budget_vs_actual.total.actual", "r12.sources.top_channel"):
            assert f'data-metric="{mid}"' in html, mid
        assert 'data-pending="online"' in html          # social/ads not ingested
        assert 'data-narrative="are-we-growing"' in html  # the month's story is placed
        assert res.gate_report and res.gate_report.exists()
        gate_text = res.gate_report.read_text()
        assert "orphaned_number" not in gate_text
        failures = [l for l in gate_text.split("## Warnings")[0].splitlines() if l.startswith("| `")]
        assert failures == [], failures
        assert res.gate_ok is True and res.ok
        assert res.unused_metrics == [], "every registered figure is displayed on some page"


class TestDrift:
    def test_a_fresh_sidecar_that_disagrees_with_the_acknowledged_value_is_new(self, tmp_path):
        from datetime import datetime, timezone
        store = SnapshotStore(tmp_path)
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        store.write_open("2026-06", "cohorts_m1",
                         {"customers": 67, "m1_net_revenue": 130063, "repeat_revenue_live": 100,
                          "live_at_last_pull": {"customers": 63, "m1_net_revenue": 125590.58}},
                         query_id="q", query_hash_="h", row_count=1, pulled_at=now, source="t")
        store.promote("2026-06", "cohorts_m1", as_of=SEP5, promoted_by="t", note="held")
        report, new = b.detect_frozen_drift(store, SEP5)
        assert report.breaches and new == [], "known at promotion -> acknowledged"
        store.write_open("2026-06", "cohorts_m1_live",
                         {"repeat_revenue_live": 200, "live_at_last_pull": {"customers": 60, "m1_net_revenue": 120000}},
                         query_id="q", query_hash_="h", row_count=1, pulled_at=now, source="t")
        report, new = b.detect_frozen_drift(store, SEP5)
        assert {(f.metric, f.live_value) for f in new} == {("m1_net_revenue", D("120000")), ("customers", D("60"))}

    def test_repeat_revenue_prefers_the_live_sidecar(self, tmp_path):
        from datetime import datetime, timezone
        store = SnapshotStore(tmp_path)
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        store.write_open("2026-06", "cohorts_m1", {"customers": 67, "m1_net_revenue": 130063, "repeat_revenue_live": 100},
                         query_id="q", query_hash_="h", row_count=1, pulled_at=now, source="t")
        store.promote("2026-06", "cohorts_m1", as_of=SEP5, promoted_by="t")
        store.write_open("2026-06", "cohorts_m1_live", {"repeat_revenue_live": 2500, "live_at_last_pull": {}},
                         query_id="q", query_hash_="h", row_count=1, pulled_at=now, source="t")
        cohorts, sources = b._load_cohorts(store)
        assert cohorts["2026-06"].m1_net == D("130063")                  # frozen M1 held
        assert cohorts["2026-06"].revenue_to_date == D("130063") + D("2500")
        assert "sidecar" in sources["2026-06"]


class TestChangeLog:
    def _series(self, values, kind="count", hib=True):
        return b.Series("k", "Thing", kind, {m: D(str(v)) for m, v in values.items()}, hib)

    def test_percent_metric_change_is_relative(self):
        s = self._series({"2026-07": "45.6", "2026-08": "55.7"}, kind="pct")
        row = b.change_log_rows([s], "2026-08")[0]
        assert row["change_pct"] == D("22.1")                   # never +10.1 points
        assert row["prior"] == "45.6%" and row["new"] == "55.7%"
        assert row["direction"].endswith("better")

    def test_lower_is_better_is_honoured(self):
        s = self._series({"2026-07": "10000", "2026-08": "8489.18"}, kind="currency", hib=False)
        row = b.change_log_rows([s], "2026-08")[0]
        assert row["direction"] == "↓ better" and row["prior"] == "$10,000.00"

    def test_zero_baseline_is_named_not_computed(self):
        row = b.change_log_rows([self._series({"2026-07": 0, "2026-08": 5})], "2026-08")[0]
        assert row["change_pct"] is None and "zero" in row["direction"]

    def test_threshold_needs_enough_history(self):
        vals = {f"2026-0{m}": 100 for m in range(5, 9)}
        assert b.variance_threshold(self._series(vals), "2026-08") is None
        row = b.change_log_rows([self._series(vals)], "2026-08")[0]
        assert row["threshold_pct"] is None and row["exceeds"] is None

    def test_threshold_is_two_sd_of_trailing_mom_changes(self):
        months = [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 9)]
        vals = {m: 100 + (5 if i % 2 else -5) for i, m in enumerate(months)}   # alternating +-, sd of deltas known
        s = self._series(vals)
        changes = [delta(vals[bm], vals[am]) for am, bm in zip(months, months[1:]) if bm <= "2026-07"]
        changes = changes[-12:]
        mu = sum(changes) / len(changes)
        sd = (sum((c - mu) ** 2 for c in changes) / len(changes)).sqrt()
        assert b.variance_threshold(s, "2026-08") == (sd * 2).quantize(D("0.1"))

    def test_configured_threshold_overrides(self):
        s = self._series({"2026-07": 100, "2026-08": 130})
        row = b.change_log_rows([s], "2026-08", {"k": D("20")})[0]
        assert row["threshold_source"] == "configured" and row["exceeds"] is True

    def test_markdown_lists_flagged_metrics(self, tmp_path):
        s = self._series({"2026-07": 100, "2026-08": 130})
        rows = b.change_log_rows([s], "2026-08", {"k": D("20")})
        p = b.write_change_log(rows, "2026-08", SEP5, tmp_path)
        text = p.read_text()
        assert "| Thing |" in text and "+30.0%" in text and "**YES**" in text and "Flagged: Thing." in text


class TestOneDeltaRule:
    def test_build_and_ingest_pass_the_ast_scan(self):
        from src.validate.code import check_code, SRC_ROOT
        findings = [f for f in check_code(SRC_ROOT) if "build.py" in f.file or "ingest" in f.file]
        assert findings == [], [f.line() for f in findings]


def test_cli_exit_code_follows_ok(tmp_path, monkeypatch):
    good = b.BuildResult(SEP5, "2026-08", tmp_path, rendered=[tmp_path / "x"], gate_ok=True)
    monkeypatch.setattr(b, "build", lambda *a, **k: good)
    assert b.main(["--as-of", "2026-09-05", "--dist", str(tmp_path / "d")]) == 0
    bad = b.BuildResult(SEP5, "2026-08", tmp_path, skipped={"executive": "missing ids"}, gate_ok=True)
    monkeypatch.setattr(b, "build", lambda *a, **k: bad)
    assert b.main(["--as-of", "2026-09-05"]) == 1
    failed_gate = b.BuildResult(SEP5, "2026-08", tmp_path, rendered=[tmp_path / "x"], gate_ok=False)
    monkeypatch.setattr(b, "build", lambda *a, **k: failed_gate)
    assert b.main(["--as-of", "2026-09-05"]) == 1
