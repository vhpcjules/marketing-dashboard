"""The three pages: contracts resolve per month, population satisfies them,
and the build renders and gates all three against the committed repository."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

import src.build as b
from src.data.spend import SpendData
from src.populate import _clean_source, _z_two_proportions, period_ids
from src.render import contracts

SEP5 = date(2026, 9, 5)
quiet = lambda s: None  # noqa: E731


class TestContracts:
    def test_placeholders_resolve_for_any_month(self):
        c = contracts.EXECUTIVE.for_period("2026-08")
        ids = [s.metric_id for s in c.metrics]
        assert "aug26.new_customers" in ids and "jul26.new_customers" in ids and "ytd25.spend" in ids
        assert "fy26.on_track" in c.claims
        c9 = contracts.EXECUTIVE.for_period("2026-09")
        assert "sep26.new_customers" in [s.metric_id for s in c9.metrics]
        c1 = contracts.SALES.for_period("2027-01")
        assert "jan27.lead_records" in [s.metric_id for s in c1.metrics]
        assert "dec26.lead_records" in [s.metric_id for s in c1.metrics]

    def test_no_month_name_survives_unresolved(self):
        for c in contracts.ALL:
            r = c.for_period("2026-08")
            assert not any("{" in s.metric_id for s in r.metrics)
            assert not any("{" in s for s in r.claims)

    def test_period_ids(self):
        assert period_ids("2026-01") == {"cur": "jan26", "prev": "dec25", "ytd": "ytd26", "pytd": "ytd25",
                                         "fy": "fy26", "pfy": "fy25", "yy": "26", "pyy": "25"}


class TestHelpers:
    def test_clean_source_strips_netsuite_code_only(self):
        assert _clean_source("CAM37 Organic Search") == "Organic Search"
        assert _clean_source("Untracked") == "Untracked"
        assert _clean_source("Partner Referral") == "Partner Referral"

    def test_two_proportion_z(self):
        # 36/61 vs 19/61: a real spread
        assert _z_two_proportions(36, 61, 19, 61) > D("1.96")
        # 30/59 vs 33/71: noise
        assert _z_two_proportions(30, 59, 33, 71) < D("1.96")
        assert _z_two_proportions(0, 10, 0, 10) == 0


@pytest.fixture
def built(tmp_path, monkeypatch):
    """One real build of all three pages, with a synthetic 2025 spend
    fixture only if the repo lacks 2025 monthly snapshots."""
    real = b._load_spend

    def fake(year):
        got = real(year)
        if got is not None or year != 2025:
            return got
        postings = {f"2025-{m:02d}": {"66212.0016": D("30000"), "66212.0017": D("10000")} for m in range(1, 13)}
        return SpendData(year=2025, postings=postings, corrections=[], budget={"accounts": {}},
                         _meta={"months": {m: {"frozen": True} for m in postings}, "budget": {}})
    monkeypatch.setattr(b, "_load_spend", fake)
    res = b.build(SEP5, tmp_path / "dist", reports_dir=tmp_path / "reports", log=quiet)
    return res, {p.parent.name: p.read_text() for p in res.rendered}


class TestAllThreePages:
    def test_every_page_renders_and_the_gate_passes(self, built):
        res, pages = built
        assert res.skipped == {}, res.skipped
        assert set(pages) == {"executive", "marketing-ops", "sales"}
        assert res.gate_ok is True and res.ok

    def test_shared_ids_render_identically_across_pages(self, built):
        import re
        _, pages = built
        seen = {}
        for slug, html in pages.items():
            for mid, text in re.findall(r'data-metric="([^"]+)"[^>]*>([^<]*)</span>', html):
                if mid.endswith("__delta"):
                    continue
                seen.setdefault(mid, {})[slug] = text
        for mid, by_page in seen.items():
            assert len(set(by_page.values())) == 1, (mid, by_page)

    def test_marketing_ops_has_its_tables_and_narrative(self, built):
        _, pages = built
        html = pages["marketing-ops"]
        for table in ("paid_media_recon", "budget_vs_actual", "yoy_channel", "m13_cohorts", "cohorts_by_age",
                      "retention_bands", "asks"):
            assert f'data-table="{table}"' in html, table
        assert 'data-metric="yoy_channel.total.current"' in html
        assert 'data-narrative="spend-detail"' in html
        assert 'data-metric="ask26.total"' in html and 'data-metric="corr26.agency_credit_pending_detail"' in html
        # the agency platform's revenue is labelled, never used as revenue
        assert 'data-metric="truad.revenue_overstatement"' in html

    def test_sales_names_reps_and_carries_denominators(self, built):
        _, pages = built
        html = pages["sales"]
        assert 'data-table="reps"' in html and 'data-metric="aug26.rep.alexis.rate"' in html
        assert 'data-metric="aug26.with_phone"' in html          # every rate carries its denominator
        assert 'data-metric="r14.unassigned_rate"' in html
        assert 'data-narrative="lead-quality"' in html

    def test_executive_never_names_a_person(self, built):
        _, pages = built
        for name in ("Alexis", "Dan ", "Parker"):
            assert name not in pages["executive"]

    def test_narratives_placed_and_retired_items_marked(self, built):
        _, pages = built
        for slug, html in pages.items():
            assert 'data-pending="narrative"' not in html, slug
            assert 'data-narrative="not-carried-forward"' in html and "data-retired" in html, slug

    def test_nothing_registered_goes_undisplayed(self, built):
        res, _ = built
        assert res.unused_metrics == []
