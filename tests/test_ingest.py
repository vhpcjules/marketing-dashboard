"""Ingest adapters, with fake executors standing in for the MCP tools.

The point of the executor seam is that the shaping, validation and refusal
logic can be proven here without a connector. Every refusal below is a real
defect: SQL injection through a parameter, a re-pull of a frozen month, a
partial month written as final (the v1 LinkedIn 2x understatement), a
traffic campaign judged on leads, a manual file with a field missing.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from src.freeze import FreezeError, SnapshotStore, query_hash
from src.ingest import manual as manual_mod
from src.ingest import netsuite as ns
from src.ingest import supermetrics as sm
from src.ingest.common import MissingManualInput, Pull, dec, jsonable
from src.ingest.queries import (ParameterError, QueryError, load_query, month_bounds, months_to_pull,
                                render_value, statements, strip_comments, substitute)

SEP5 = date(2026, 9, 5)
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _seed(store, period, domain, body, frozen=False):
    store.write_open(period, domain, body, query_id="q", query_hash_="abc", row_count=1,
                     pulled_at=NOW, source="test")
    if frozen:
        store.promote(period, domain, as_of=SEP5, promoted_by="test", note="seed")


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

class TestParameterSubstitution:
    @pytest.mark.parametrize("bad", [
        "2026-08-01' OR '1'='1",          # quote
        "2; DROP TABLE transaction",      # semicolon
        "x -- comment",                   # comment marker
        "a\nb",                           # newline
        "50%",                            # wildcard
        "(2)",                            # parenthesis
    ])
    def test_injection_characters_are_refused(self, bad):
        with pytest.raises(ParameterError):
            substitute("SELECT :v", {"v": bad})

    def test_dates_are_validated_and_quoted(self):
        assert render_value("d", date(2026, 8, 1)) == "'2026-08-01'"
        assert render_value("d", "2026-08-01") == "'2026-08-01'"
        with pytest.raises(ParameterError, match="not a valid"):
            render_value("d", "2026-02-30")

    def test_integers_are_bare(self):
        assert render_value("i", 2) == "2"
        assert render_value("i", "14") == "14"
        with pytest.raises(ParameterError):
            render_value("i", D("2.5"))

    def test_bool_and_float_refused(self):
        with pytest.raises(ParameterError):
            render_value("b", True)
        with pytest.raises(ParameterError):
            render_value("f", 2.0)

    def test_missing_parameter_raises(self):
        with pytest.raises(ParameterError, match="not supplied"):
            substitute("WHERE x >= TO_DATE(:date_from, 'YYYY-MM-DD')", {})

    def test_unused_parameter_raises_to_catch_typos(self):
        with pytest.raises(ParameterError, match="not referenced"):
            substitute("WHERE a = :date_to", {"date_to": "2026-08-01", "date_too": "2026-09-01"})

    def test_safe_string_is_quoted(self):
        assert render_value("s", "CustInvc") == "'CustInvc'"

    def test_format_masks_are_untouched(self):
        sql = substitute("TO_DATE(:d, 'YYYY-MM-DD')", {"d": "2026-08-01"})
        assert sql == "TO_DATE('2026-08-01', 'YYYY-MM-DD')"


class TestQueryText:
    def test_strip_comments_drops_whole_and_trailing_comments(self):
        sql = "-- header\nSELECT 1 -- trailing\n  \nFROM t"
        assert strip_comments(sql) == "SELECT 1\nFROM t"

    def test_comment_marker_inside_quotes_is_kept(self):
        assert strip_comments("SELECT '--not a comment' FROM t") == "SELECT '--not a comment' FROM t"

    def test_statements_split_outside_quotes(self):
        assert statements("SELECT 'a;b'; SELECT 2;") == ["SELECT 'a;b'", "SELECT 2"]

    def test_multi_statement_file_needs_an_index(self):
        with pytest.raises(QueryError, match="statements"):
            load_query("geography_12mo", {"window_from": "2025-09-01", "window_to": "2026-09-01"})
        sql, h = load_query("geography_12mo", {"window_from": "2025-09-01", "window_to": "2026-09-01"}, statement=0)
        assert "customeraddressbook" in sql and len(h) == 16

    def test_hash_is_of_the_query_not_the_month(self, tmp_path):
        (tmp_path / "q.sql").write_text("-- v1\nSELECT * FROM t WHERE d >= TO_DATE(:d, 'YYYY-MM-DD')\n")
        a = load_query("q", {"d": "2026-08-01"}, search_path=[tmp_path])
        b = load_query("q", {"d": "2026-07-01"}, search_path=[tmp_path])
        assert a.query_hash == b.query_hash and a.sql != b.sql

    def test_hash_ignores_comment_edits_but_not_clause_edits(self, tmp_path):
        (tmp_path / "q.sql").write_text("-- v1\nSELECT a FROM t\n")
        h1 = load_query("q", search_path=[tmp_path]).query_hash
        (tmp_path / "q.sql").write_text("-- v2, reworded\nSELECT a FROM t\n")
        assert load_query("q", search_path=[tmp_path]).query_hash == h1
        (tmp_path / "q.sql").write_text("SELECT a, b FROM t\n")
        assert load_query("q", search_path=[tmp_path]).query_hash != h1

    def test_hash_matches_freeze_query_hash_of_the_stripped_statement(self, tmp_path):
        (tmp_path / "q.sql").write_text("-- c\nSELECT 1\n")
        assert load_query("q", search_path=[tmp_path]).query_hash == query_hash("SELECT 1")

    def test_repo_queries_all_load(self):
        for name, params in {
            "marketing_spend_monthly": {"subsidiary_id": 2, "date_from": "2026-08-01", "date_to": "2026-09-01"},
            "cohorts_m13": {"cohort_from": "2026-05-01", "cohort_to": "2026-06-01"},
            "cohorts_m1": {"cohort_from": "2026-08-01", "cohort_to": "2026-09-01"},
            "cohorts_revenue_to_date": {"cohort_from": "2026-08-01", "cohort_to": "2026-09-01", "through": "2026-09-06"},
        }.items():
            sql, _ = load_query(name, params)
            assert ":" not in sql.replace("'YYYY-MM-DD'", "").replace("::", ""), f"{name} left a placeholder"

    def test_path_traversal_is_not_a_query_name(self):
        with pytest.raises(QueryError):
            load_query("../freeze")


class TestChunking:
    def test_month_bounds_are_half_open(self):
        assert month_bounds("2026-08") == (date(2026, 8, 1), date(2026, 9, 1))
        assert month_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))

    def test_months_to_pull_skips_frozen_only(self, tmp_path):
        store = SnapshotStore(tmp_path)
        _seed(store, "2026-06", "cohorts_m1", {"m1_net_revenue": 1}, frozen=True)
        _seed(store, "2026-07", "cohorts_m1", {"m1_net_revenue": 1}, frozen=False)   # closed, not promoted
        months = ["2026-06", "2026-07", "2026-08", "2026-09"]                          # 09 is open
        assert months_to_pull(store, "cohorts_m1", months, SEP5) == ["2026-07", "2026-08", "2026-09"]

    def test_months_to_pull_against_the_repo_store(self):
        store = SnapshotStore()
        got = months_to_pull(store, "marketing_spend", [f"2026-0{i}" for i in range(1, 9)], SEP5)
        assert got == ["2026-08"], "Jan-Jul 2026 spend is frozen; only August may be re-pulled"


# ---------------------------------------------------------------------------
# common
# ---------------------------------------------------------------------------

class TestCarrier:
    def test_decimals_round_trip_through_json(self):
        body = jsonable({"a": D("5866.79"), "b": D("82"), "c": D("85123.40"), "d": D("0.1") + D("0.2")})
        back = json.loads(json.dumps(body))
        assert back["b"] == 82 and isinstance(back["b"], int)
        for k, want in (("a", "5866.79"), ("c", "85123.40"), ("d", "0.3")):
            assert D(str(back[k])) == D(want)

    def test_float_in_a_body_is_refused(self):
        with pytest.raises(TypeError):
            jsonable({"x": 1.5})

    def test_dec_refuses_floats_and_accepts_strings(self):
        assert dec("1,234.50") == D("1234.50")
        with pytest.raises(TypeError):
            dec(1.5)
        with pytest.raises(ValueError):
            dec("abc")


# ---------------------------------------------------------------------------
# NetSuite
# ---------------------------------------------------------------------------

def _ns_fake(sql: str) -> list[dict]:
    """Rows shaped like the MCP tool returns them: strings, never floats."""
    if "acctnumber" in sql:
        return [{"month": "2026-08", "account": "66212.0016", "account_name": "Google", "amount": "5866.79"},
                {"month": "2026-08", "account": "66212.0017", "account_name": "Meta", "amount": "2596.42"}]
    if "revenue_to_date" in sql:
        return [{"customers": "82", "revenue_to_date": "96501.85", "transactions": "120"}]
    if "customers_m13" in sql:          # before customers_m1: it is a substring of this
        return [{"customers_m13": "79", "m13_net_revenue": "116821.00", "transactions": "200"}]
    if "customers_m1" in sql:
        return [{"customers_m1": "82", "m1_net_revenue": "85123.40", "transactions": "90"}]
    if "with_phone" in sql:
        return [{"month": "2026-07", "total_records": "276", "with_phone": "157", "with_mobile": "0", "with_alt": "0",
                 "with_any_phone": "157", "with_email": "270", "customers": "79", "assigned_records": "150",
                 "assigned_with_phone": "148", "assigned_with_email": "147"},
                {"month": "2026-08", "total_records": "317", "with_phone": "195", "with_mobile": "0", "with_alt": "0",
                 "with_any_phone": "195", "with_email": "312", "customers": "87", "assigned_records": "194",
                 "assigned_with_phone": "192", "assigned_with_email": "190"}]
    if "rep_id" in sql:
        return [{"month": "2026-08", "rep_id": "8766", "assigned": "61", "converted": "36"},
                {"month": "2026-08", "rep_id": "5803", "assigned": "72", "converted": "32"},
                {"month": "2026-08", "rep_id": "99999", "assigned": "3", "converted": "1"},
                {"month": "2026-08", "rep_id": None, "assigned": "123", "converted": "0"}]
    raise AssertionError(f"unexpected SQL: {sql[:80]}")


@pytest.fixture
def adapter():
    return ns.NetSuiteAdapter(_ns_fake)


class TestNetSuiteAdapter:
    def test_marketing_spend_body_and_hash(self, adapter):
        body, rows, h = adapter.pull_marketing_spend("2026-08")
        assert body["postings"] == {"66212.0016": 5866.79, "66212.0017": 2596.42}
        assert body["account_names"]["66212.0016"] == "Google"
        assert rows == 2
        assert h == load_query("marketing_spend_monthly", {"subsidiary_id": 2, "date_from": "2026-08-01",
                                                           "date_to": "2026-09-01"}).query_hash

    def test_out_of_scope_account_is_refused(self):
        a = ns.NetSuiteAdapter(lambda sql: [{"month": "2026-08", "account": "96212.0016", "amount": "1"}])
        with pytest.raises(ns.NetSuiteError, match="outside"):
            a.pull_marketing_spend("2026-08")

    def test_float_from_executor_is_refused(self):
        a = ns.NetSuiteAdapter(lambda sql: [{"month": "2026-08", "account": "66212.0016", "amount": 5866.79}])
        with pytest.raises(TypeError, match="float"):
            a.pull_marketing_spend("2026-08")

    def test_cohort_m1_body_shape(self, adapter):
        pull = adapter.pull_cohort_m1("2026-08", as_of=SEP5)
        assert isinstance(pull, Pull)
        b = pull.body
        assert b["customers"] == 82 and D(str(b["m1_net_revenue"])) == D("85123.40")
        assert D(str(b["repeat_revenue_live"])) == D("96501.85") - D("85123.40")
        assert b["live_at_last_pull"] == {"customers": 82, "m1_net_revenue": 85123.4, "revenue_to_date": 96501.85}
        assert pull.row_count == 2 and len(pull.query_hash) == 16

    def test_cohort_revenue_below_m1_is_impossible(self):
        def fake(sql):
            if "revenue_to_date" in sql:
                return [{"customers": "82", "revenue_to_date": "1000", "transactions": "1"}]
            return [{"customers_m1": "82", "m1_net_revenue": "85123.40", "transactions": "90"}]
        with pytest.raises(ns.NetSuiteError, match="impossible"):
            ns.NetSuiteAdapter(fake).pull_cohort_m1("2026-08", as_of=SEP5)

    def test_row_cap_is_a_refusal(self):
        a = ns.NetSuiteAdapter(lambda sql: [{"x": 1}] * ns.SUITEQL_ROW_CAP)
        with pytest.raises(ns.RowCapError):
            a.run("marketing_spend_monthly", {"subsidiary_id": 2, "date_from": "2026-08-01", "date_to": "2026-09-01"})

    def test_m13_refuses_an_open_window(self, adapter):
        # 2026-06-30 + 90 days = 2026-09-28, after Sep 5
        with pytest.raises(ns.NetSuiteError, match="90-day window"):
            adapter.pull_cohorts_m13("2026-06", as_of=SEP5)
        body, _, _ = adapter.pull_cohorts_m13("2026-05", as_of=SEP5)
        assert body["customers_m13"] == 79 and body["window_closed_on"] == "2026-08-29"

    def test_lead_quality_rates_carry_denominators(self, adapter):
        pulls = adapter.pull_lead_quality(["2026-07", "2026-08"])
        aug = pulls["2026-08"].body
        assert aug["total_records"] == 317 and aug["with_phone"] == 195
        assert D(str(aug["phone_capture_pct"])) == D("61.5")
        assert D(str(aug["conversion_pct"])) == D("27.4")
        assert D(str(aug["assigned_phone_capture_pct"])) == D("99.0")

    def test_lead_routing_buckets_known_reps_and_names_the_rest(self, adapter):
        pulls = adapter.pull_lead_routing(["2026-08"], rep_names={"99999": "New Hire"})
        reps = pulls["2026-08"].body["reps"]
        assert reps["alexis"] == {"assigned": 61, "converted": 36}
        assert reps["unassigned"]["assigned"] == 123
        assert reps["other"] == {"assigned": 3, "converted": 1, "names": ["New Hire"]}
        assert pulls["2026-08"].body["total_records"] == 61 + 72 + 3 + 123


class TestNetSuiteWrites:
    def test_open_month_is_written_with_meta(self, adapter, tmp_path):
        store = SnapshotStore(tmp_path)
        ns.ingest_marketing_spend(adapter, store, "2026-08", pulled_at=NOW)
        snap = store.read("2026-08", "marketing_spend")
        assert not snap.frozen and snap.meta["row_count"] == 2 and snap.meta["source"] == ns.SOURCE
        assert D(str(snap.body["postings"]["66212.0016"])) == D("5866.79")
        assert snap.body["account_names"]["66212.0017"] == "Meta"

    def test_frozen_cohort_gets_a_live_sidecar_and_stays_untouched(self, adapter, tmp_path):
        store = SnapshotStore(tmp_path)
        _seed(store, "2026-06", "cohorts_m1", {"customers": 67, "m1_net_revenue": 130063,
                                               "repeat_revenue_live": 100}, frozen=True)
        p = ns.ingest_cohort_m1(adapter, store, "2026-06", as_of=SEP5, pulled_at=NOW)
        assert p.name == "cohorts_m1_live.json"
        assert store.read("2026-06", "cohorts_m1").metric("m1_net_revenue") == D("130063")
        side = store.read("2026-06", "cohorts_m1_live")
        assert "live_at_last_pull" in side.body and "repeat_revenue_live" in side.body
        assert "m1_net_revenue" not in side.body, "the sidecar carries live components only"

    def test_direct_write_of_a_frozen_month_is_refused_by_the_store(self, adapter, tmp_path):
        store = SnapshotStore(tmp_path)
        _seed(store, "2026-06", "marketing_spend", {"postings": {}}, frozen=True)
        pull = adapter.pull_marketing_spend("2026-08")
        with pytest.raises(FreezeError):
            store.write_open("2026-06", "marketing_spend", pull.body, query_id="q", query_hash_=pull.query_hash,
                             row_count=pull.row_count, pulled_at=NOW, source="t")


# ---------------------------------------------------------------------------
# Supermetrics
# ---------------------------------------------------------------------------

def _daily(spec: sm.QuerySpec, *, last_day: date | None = None, extra=None) -> sm.QueryResult:
    a, b = spec.start_date, last_day or spec.end_date
    rows, d = [], a
    while d <= b:
        row = {"date": d.isoformat()}
        row.update(extra(d) if extra else {})
        rows.append(row)
        d += timedelta(days=1)
    return sm.QueryResult(tuple(rows), spec.start_date, spec.end_date)


def _linkedin_exec(partial: bool = False):
    def ex(spec):
        if not spec.dated:
            return sm.QueryResult(({"total_share_impressions": "1200"},), spec.start_date, spec.end_date)
        last = date(2026, 8, 20) if partial else None
        return _daily(spec, last_day=last, extra=lambda d: {"page_impressions": "100", "page_engagements": "5",
                                                            "page_engagement_rate": "5"})
    return ex


class TestFieldGuards:
    def test_linkedin_families_never_mixed(self):
        with pytest.raises(sm.FieldGuardError, match="never be pulled in one query"):
            sm.QuerySpec(sm.LINKEDIN, ("page_impressions", "total_share_impressions"), date(2026, 8, 1), date(2026, 8, 31))

    def test_share_impressions_cannot_take_a_date_dimension(self):
        with pytest.raises(sm.FieldGuardError, match="cannot be broken down by date"):
            sm.QuerySpec(sm.LINKEDIN, ("date", "total_share_impressions"), date(2026, 8, 1), date(2026, 8, 31))

    def test_meta_pixel_lead_field_is_refused(self):
        with pytest.raises(sm.FieldGuardError, match="entirely NULL"):
            sm.QuerySpec(sm.META_ADS, ("date", "campaignobjective", "offsite_conversions_fb_pixel_lead"),
                         date(2026, 8, 1), date(2026, 8, 31))

    def test_meta_lead_metric_requires_objective(self):
        with pytest.raises(sm.FieldGuardError, match="campaignobjective"):
            sm.QuerySpec(sm.META_ADS, ("date", sm.META_LEAD_FIELD), date(2026, 8, 1), date(2026, 8, 31))

    def test_the_shipped_specs_pass_their_own_guards(self):
        for fn in (sm.linkedin_page_spec, sm.linkedin_share_spec, sm.instagram_spec, sm.meta_ads_spec):
            spec = fn("2026-08")
            assert spec.tool_args()["date_range_type"] == "custom"
            assert spec.tool_args()["start_date"] == "2026-08-01" and spec.tool_args()["end_date"] == "2026-08-31"
        assert "campaignobjective" in sm.meta_ads_spec("2026-08").fields

    def test_account_ids_are_the_real_ones(self):
        assert sm.LINKEDIN.account == "6735901"
        assert sm.INSTAGRAM.account == "17841402384139665"
        assert sm.META_ADS.account == "act_1162719948574137"

    def test_fingerprint_ignores_dates(self):
        assert sm.linkedin_page_spec("2026-08").fingerprint() == sm.linkedin_page_spec("2026-07").fingerprint()
        assert sm.linkedin_page_spec("2026-08").fingerprint() != sm.linkedin_share_spec("2026-08").fingerprint()


class TestCoverageGuard:
    def test_partial_month_is_refused(self):
        a = sm.SupermetricsAdapter(_linkedin_exec(partial=True))
        with pytest.raises(sm.CoverageError, match="rows span"):
            a.pull_linkedin("2026-08", as_of=SEP5)

    def test_month_not_over_is_refused(self):
        a = sm.SupermetricsAdapter(_linkedin_exec())
        with pytest.raises(sm.CoverageError, match="not over"):
            a.pull_linkedin("2026-09", as_of=SEP5)
        with pytest.raises(sm.CoverageError, match="not over"):
            a.pull_linkedin("2026-08", as_of=date(2026, 8, 31))

    def test_result_range_must_be_exactly_the_month(self):
        def ex(spec):
            r = _linkedin_exec()(spec)
            return sm.QueryResult(r.rows, spec.start_date, spec.end_date - timedelta(days=1))
        with pytest.raises(sm.CoverageError, match="not exactly the month"):
            sm.SupermetricsAdapter(ex).pull_linkedin("2026-08", as_of=SEP5)

    def test_refusal_writes_nothing(self, tmp_path):
        store = SnapshotStore(tmp_path)
        a = sm.SupermetricsAdapter(_linkedin_exec(partial=True))
        with pytest.raises(sm.CoverageError):
            sm.ingest_supermetrics(a, store, "linkedin", "2026-08", as_of=SEP5)
        assert not store.exists("2026-08", "linkedin")

    def test_full_month_is_written(self, tmp_path):
        store = SnapshotStore(tmp_path)
        a = sm.SupermetricsAdapter(_linkedin_exec())
        sm.ingest_supermetrics(a, store, "linkedin", "2026-08", as_of=SEP5, pulled_at=NOW)
        b = store.read("2026-08", "linkedin").body
        assert b["page_statistics"]["page_impressions"] == 3100
        assert b["page_statistics"]["days_covered"] == 31
        assert b["share_statistics"]["total_share_impressions"] == 1200
        assert "never summed" in b["note"]


class TestMetaLeads:
    def test_leads_counted_for_lead_campaigns_only(self):
        def ex(spec):
            rows = []
            for d in (date(2026, 8, 1), date(2026, 8, 31)):
                rows.append({"date": d.isoformat(), "campaignname": "Leads A", "campaignobjective": "OUTCOME_LEADS",
                             "cost": "100.00", "impressions": "1000", "clicks": "50", sm.META_LEAD_FIELD: "4"})
                rows.append({"date": d.isoformat(), "campaignname": "Traffic B", "campaignobjective": "OUTCOME_TRAFFIC",
                             "cost": "50.00", "impressions": "2000", "clicks": "80", sm.META_LEAD_FIELD: "9"})
            return sm.QueryResult(tuple(rows), spec.start_date, spec.end_date)
        body, rows, _ = sm.SupermetricsAdapter(ex).pull_meta_ads("2026-08", as_of=SEP5)
        assert body["lead_campaigns"]["leads"] == 8, "the traffic campaign's 18 'leads' are not counted"
        assert body["lead_campaigns"]["spend"] == 200
        assert D(str(body["lead_campaigns"]["cost_per_lead"])) == D("25.00")
        traffic = next(c for c in body["campaigns"] if c["objective"] == "OUTCOME_TRAFFIC")
        assert traffic["leads"] is None and traffic["judged_on_leads"] is False
        assert body["spend"] == 300 and body["objectives_present"] == ["OUTCOME_LEADS", "OUTCOME_TRAFFIC"]


# ---------------------------------------------------------------------------
# Manual
# ---------------------------------------------------------------------------

class TestManual:
    def test_absent_file_is_a_marker_not_an_error(self, tmp_path):
        got = manual_mod.load_manual("gmb", "2026-08", tmp_path)
        assert isinstance(got, MissingManualInput)
        assert "gmb_2026-08.json" in got.reason and "August 2026" in got.reason

    def test_marker_reason_names_a_repo_relative_folder(self):
        got = manual_mod.load_manual("gmb", "2026-08")          # repo root: no file exists for August yet
        assert isinstance(got, MissingManualInput)
        assert "data/manual/2026/" in got.reason and "/home/" not in got.reason

    def test_ingest_passes_the_marker_through(self, tmp_path):
        store = SnapshotStore(tmp_path / "snap")
        out = manual_mod.ingest_manual(store, "hotjar", "2026-08", root=tmp_path / "manual")
        assert isinstance(out, MissingManualInput)
        assert not store.exists("2026-08", "hotjar")

    def test_json_file_is_written_as_a_snapshot(self, tmp_path):
        root = tmp_path / "manual"; (root / "2026").mkdir(parents=True)
        (root / "2026" / "gmb_2026-08.json").write_text(json.dumps({
            "period": "2026-08", "impressions": 12345, "website_clicks": 210, "calls": 44,
            "direction_requests": 89, "source": "GBP performance export"}))
        store = SnapshotStore(tmp_path / "snap")
        p = manual_mod.ingest_manual(store, "gmb", "2026-08", root=root, pulled_at=NOW)
        snap = store.read("2026-08", "gmb")
        assert snap.metric("impressions") == D("12345") and snap.body["notes"]["source"] == "GBP performance export"
        assert snap.meta["query_id"] == "manual:gmb" and not snap.frozen and p.exists()

    def test_csv_header_row_shape(self, tmp_path):
        root = tmp_path / "manual"; (root / "2026").mkdir(parents=True)
        (root / "2026" / "hotjar_2026-08.csv").write_text(
            "recordings,rage_click_recordings,feedback_responses\n1500,37,12\n")
        got = manual_mod.load_manual("hotjar", "2026-08", root)
        assert got.values == {"recordings": D("1500"), "rage_click_recordings": D("37"), "feedback_responses": D("12")}

    def test_csv_key_value_shape(self, tmp_path):
        root = tmp_path / "manual"; (root / "2026").mkdir(parents=True)
        (root / "2026" / "hotjar_2026-08.csv").write_text(
            "field,value\nrecordings,1500\nrage_click_recordings,37\nfeedback_responses,12\n")
        assert manual_mod.load_manual("hotjar", "2026-08", root).values["recordings"] == D("1500")

    def test_missing_required_field_raises(self, tmp_path):
        root = tmp_path / "manual"; (root / "2026").mkdir(parents=True)
        (root / "2026" / "gmb_2026-08.json").write_text(json.dumps({"impressions": 1, "calls": 2}))
        with pytest.raises(manual_mod.ManualInputError, match="missing required"):
            manual_mod.load_manual("gmb", "2026-08", root)

    def test_negative_or_non_numeric_raises(self, tmp_path):
        root = tmp_path / "manual"; (root / "2026").mkdir(parents=True)
        f = root / "2026" / "gmb_2026-08.json"
        f.write_text(json.dumps({"impressions": -1, "website_clicks": 1, "calls": 1, "direction_requests": 1}))
        with pytest.raises(manual_mod.ManualInputError, match="negative"):
            manual_mod.load_manual("gmb", "2026-08", root)
        f.write_text(json.dumps({"impressions": "lots", "website_clicks": 1, "calls": 1, "direction_requests": 1}))
        with pytest.raises(manual_mod.ManualInputError):
            manual_mod.load_manual("gmb", "2026-08", root)

    def test_period_mismatch_raises(self, tmp_path):
        root = tmp_path / "manual"; (root / "2026").mkdir(parents=True)
        (root / "2026" / "gmb_2026-08.json").write_text(json.dumps(
            {"period": "2026-07", "impressions": 1, "website_clicks": 1, "calls": 1, "direction_requests": 1}))
        with pytest.raises(manual_mod.ManualInputError, match="period"):
            manual_mod.load_manual("gmb", "2026-08", root)

    def test_unknown_domain_raises(self, tmp_path):
        with pytest.raises(manual_mod.ManualInputError):
            manual_mod.load_manual("ga4", "2026-08", tmp_path)

    def test_repo_has_no_manual_inputs_for_august_yet(self):
        # Documents the current state: the build must render these as pending.
        for domain in manual_mod.REQUIRED_FIELDS:
            assert isinstance(manual_mod.load_manual(domain, "2026-08"), MissingManualInput)
