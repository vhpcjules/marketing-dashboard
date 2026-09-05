"""The v1 dashboards are the adversarial corpus.

reference/v1/*.html are the seven pages that shipped before this codebase
existed, and every check in src/validate maps to a bug that was found in
them after the fact. This file pins those bugs as EXPECTED FINDINGS: if a
check stops finding the bug it was written for, the check is broken, however
green the synthetic fixtures in test_validate.py are.

The last test asserts the gate FAILS on the corpus. A validation suite that
passes v1 unchanged is not working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.validate import language, narrative, numbers, structural
from src.validate.dom import parse
from src.validate.findings import FAIL, WARN
from src.validate.gate import run_gate

V1 = Path(__file__).resolve().parents[1] / "reference" / "v1"
FILES = sorted(p.name for p in V1.glob("*.html"))

# Each page's own reporting period, from its headline. The gate takes one
# period per dist; the corpus predates the build so we pass the Leadership
# page's period (July 2026) and assert on that page.
LEADERSHIP_PERIOD = "2026-07"


@pytest.fixture(scope="module")
def docs():
    return {name: parse((V1 / name).read_text()) for name in FILES}


@pytest.fixture(scope="module")
def gate_report():
    return run_gate(V1, LEADERSHIP_PERIOD)


def test_corpus_is_present():
    assert len(FILES) == 7, FILES


class TestLeadershipDashboard:
    def test_overall_m1_revenue_does_not_equal_sum_of_subtotals(self, docs):
        fs = numbers.check_breakdown_tables(docs["Leadership_Dashboard.html"], "ld")
        m1 = [f for f in fs if "M1 Revenue" in f.message and "OVERALL" in f.message]
        assert len(m1) == 1, [f.message for f in fs]
        # $214,723 + $568,843 = $783,566 against a stated $1,043,816: gap $260,250.
        assert "783,566" in m1[0].message
        assert "1,043,816" in m1[0].message
        assert "gap 260,250" in m1[0].message
        assert "$214,723 | $568,843" in m1[0].evidence
        # The other five columns of that table foot correctly; no false positives.
        assert len(fs) == 1

    def test_js_emits_delta_flat_which_css_never_styles(self, docs):
        fs = structural.check_undefined_classes(docs["Leadership_Dashboard.html"], "ld")
        assert len(fs) == 1 and fs[0].severity == WARN
        assert "'flat'" in fs[0].message and "delta" in fs[0].message

    def test_stale_fallback_text_names_may_and_april_on_a_july_page(self, docs):
        fs = narrative.check_stale_months(docs["Leadership_Dashboard.html"], "ld", LEADERSHIP_PERIOD)
        stale = [f for f in fs if "Latest month (May 2026) vs. previous month (April 2026)" in f.evidence]
        assert {f.message.split("'")[1] for f in stale} == {"May", "April"}
        assert all(f.severity == WARN for f in stale)
        # The M1 tile compared "vs April" while its neighbours compared "vs June".
        assert any("26.0% vs April" in f.evidence for f in fs)
        # And June/July, the reporting and prior months, are not flagged.
        assert not any(f.message.split("'")[1] in ("June", "July", "Jun", "Jul") for f in fs)

    def test_defines_its_own_delta_helper(self):
        hs = structural.delta_helper_definitions((V1 / "Leadership_Dashboard.html").read_text(), "ld")
        # ldDeltaText subtracts; ldDeltaClass only compares and picks a colour.
        assert [h.name for h in hs] == ["ldDeltaText"]


class TestMarketingActivity:
    def test_interaction_breakdown_does_not_sum_to_stated_total(self, docs):
        fs = numbers.check_component_lists(docs["Marketing_Activity.html"], "ma")
        assert len(fs) == 1
        assert fs[0].evidence == "240 likes · 63 comments · 34 saves · 29 shares"
        assert "sums to 366" in fs[0].message and "stated total is 375" in fs[0].message

    def test_email_table_totals_foot(self, docs):
        # The campaign table's Total row is right; the check must not invent a bug.
        assert numbers.check_breakdown_tables(docs["Marketing_Activity.html"], "ma") == []


class TestMarketingPipelineForSales:
    def test_static_tile_prints_a_point_difference_with_a_percent_sign(self, docs):
        fs = language.check_percent_point_deltas(docs["Marketing_Pipeline_for_Sales.html"], "mp")
        phone = [f for f in fs if "54%" in f.message and "42%" in f.message]
        assert len(phone) == 1
        assert "'+12%'" in phone[0].message and "28.6%" in phone[0].message
        # The conversion tile has the same bug: 32% vs 25% printed as "+7%".
        conv = [f for f in fs if "32%" in f.message and "25%" in f.message]
        assert len(conv) == 1 and "'+7%'" in conv[0].message
        # Avg deal size $1,026 vs $872 "+18%" is a correct relative change.
        assert len(fs) == 2

    def test_defines_its_own_delta_helper(self):
        hs = structural.delta_helper_definitions((V1 / "Marketing_Pipeline_for_Sales.html").read_text(), "mp")
        assert [h.name for h in hs] == ["fmtDelta"]


class TestSocialMediaPerformance:
    def test_table_says_75_new_followers_but_chart_array_says_74(self, docs):
        fs = numbers.check_tables_against_arrays(docs["Social_Media_Performance.html"], "sm")
        hit = [f for f in fs if "LI_NEW_FOLLOWERS[2] is 74" in f.message]
        assert len(hit) == 1
        assert "'Mar 2026'" in hit[0].message and "shows 75" in hit[0].message

    def test_restated_impressions_never_reached_the_table(self, docs):
        # The script comment says impressions were RESTATED 2026-08-18; the
        # chart array was updated, the table beside it was not.
        fs = numbers.check_tables_against_arrays(docs["Social_Media_Performance.html"], "sm")
        imps = {f.message.split("'")[1]: f for f in fs if "'Impressions'" in f.message}
        assert set(imps) == {"Feb 2026", "Mar 2026", "Apr 2026"}
        assert "shows 254 but chart array LI_IMPRESSIONS[2] is 508" in imps["Mar 2026"].message

    def test_meta_table_keyed_on_month_and_platform_is_not_compared(self, docs):
        fs = numbers.check_tables_against_arrays(docs["Social_Media_Performance.html"], "sm")
        assert not any("table 3" in f.message for f in fs)

    def test_engagement_axis_clips_the_plotted_data(self, docs):
        fs = structural.check_chart_clipping(docs["Social_Media_Performance.html"], "sm")
        assert len(fs) == 1
        f = fs[0]
        assert "'liTrend'" in f.message and "'y1'" in f.message and "max: 10" in f.message
        assert "21.08" in f.message and "14.31" in f.evidence

    def test_defines_its_own_delta_helper(self):
        hs = structural.delta_helper_definitions((V1 / "Social_Media_Performance.html").read_text(), "sm")
        assert [h.name for h in hs] == ["smDelta"]


class TestAcrossTheCorpus:
    def test_three_delta_helpers_across_seven_pages(self):
        helpers = []
        for name in FILES:
            helpers += structural.delta_helper_definitions((V1 / name).read_text(), name)
        assert sorted(h.name for h in helpers) == ["fmtDelta", "ldDeltaText", "smDelta"]
        fs = structural.check_delta_helpers(helpers)
        assert len(fs) == 1 and fs[0].severity == FAIL
        assert "3 client-side delta functions" in fs[0].message

    def test_chart_js_is_loaded_from_cdnjs(self, docs):
        # The spec says all seven; two pages (index, Marketing_Activity) have
        # no charts and load nothing. Every page that loads Chart.js loads it
        # from cdnjs, and each is a warning.
        with_charts = {}
        for name, doc in docs.items():
            fs = structural.check_external_dependencies(doc, name)
            if fs:
                with_charts[name] = fs
        assert set(with_charts) == {
            "Budget_Performance.html", "Leadership_Dashboard.html", "Marketing_Pipeline_for_Sales.html",
            "Social_Media_Performance.html", "YoY_Performance.html",
        }
        for fs in with_charts.values():
            assert len(fs) == 1 and fs[0].severity == WARN
            assert "external dependency" in fs[0].message and "cdnjs.cloudflare.com" in fs[0].message
        # index.html and Marketing_Activity.html genuinely have no external scripts.
        for name in ("index.html", "Marketing_Activity.html"):
            assert not docs[name].find_all("script", attr="src")

    def test_every_canvas_is_bound_and_every_chart_lookup_resolves(self, docs):
        # v1 got this right; the check must not manufacture failures on 25 charts.
        for name, doc in docs.items():
            assert structural.check_canvas_bindings(doc, name) == [], name

    def test_yoy_cohort_table_does_not_foot(self, docs):
        fs = numbers.check_breakdown_tables(docs["YoY_Performance.html"], "yoy")
        assert len(fs) == 1 and "gap -2" in fs[0].message

    def test_budget_tables_foot(self, docs):
        # Both Budget tables (with &minus; signs and a "new" cell) add up.
        assert numbers.check_breakdown_tables(docs["Budget_Performance.html"], "bp") == []

    def test_the_word_points_is_used_in_prose(self, docs):
        hits = {n: language.check_forbidden_terms(d, n) for n, d in docs.items()}
        assert len(hits["Marketing_Activity.html"]) == 3 and len(hits["YoY_Performance.html"]) == 2
        assert all("'point" in f.message for fs in hits.values() for f in fs)


class TestGateFailsOnV1:
    def test_gate_fails_with_many_distinct_findings(self, gate_report, capsys):
        r = gate_report
        assert r.ok is False
        assert len(r.files) == 7
        distinct = {(f.check, f.file, f.message) for f in r.failures}
        assert len(distinct) >= 10
        # Every check family that has a v1 bug reports it.
        assert set(r.by_check()) >= {
            "numbers.breakdown_table", "numbers.component_list", "numbers.table_vs_chart_array",
            "language.percent_point_delta", "language.forbidden_term",
            "structural.chart_clipping", "structural.delta_helpers", "narrative.orphaned_number",
        }
        assert {f.check for f in r.warnings} >= {
            "structural.external_dependency", "structural.undefined_class", "narrative.stale_month",
        }
        # Print the list so a human can read what the gate found in v1.
        with capsys.disabled():
            print("\n" + r.console(max_per_check=6))

    def test_orphaned_numbers_flood_is_expected_on_hand_typed_pages(self, gate_report):
        # v1 is hand-typed prose; every figure is orphaned. Our templates
        # must carry data-metric / data-claim on every figure instead.
        assert gate_report.by_check()["narrative.orphaned_number"] > 500

    def test_markdown_report_lists_the_headline_bugs(self, gate_report):
        md = gate_report.to_markdown()
        assert "**FAIL**" in md
        for needle in ("gap 260,250", "sums to 366", "LI_NEW_FOLLOWERS[2] is 74",
                       "max: 10", "3 client-side delta functions", "'+12%'"):
            assert needle in md, needle
