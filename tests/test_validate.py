"""Unit tests for the validation layer, one class per check family.

Every check has a positive case (a clean fixture produces no finding) and a
negative case (the v1 bug shape produces exactly the expected finding). The
trap cases for the forbidden-string check are here too: "phone capture",
"opportunity" and "font-size:12pt" must NOT fire, "+10.1 pts", "12 points"
and "percentage point" MUST.
"""

from __future__ import annotations

import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from src.validate import Finding, GateReport, run_gate
from src.validate import code, language, narrative, numbers, structural
from src.validate.dom import parse, tag_counts
from src.validate.findings import FAIL, WARN
from src.validate.gate import main as gate_main
from src.validate.js import chart_blocks, numeric_arrays, string_arrays
from src.validate.months import find_month_mentions, parse_month_label
from src.validate.numeric import parse_number

REPO = Path(__file__).resolve().parents[1]


def page(body: str = "", head: str = "", script: str = "") -> str:
    sc = f"<script>{script}</script>" if script else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>t</title>{head}</head>"
            f"<body>{body}{sc}</body></html>")


def checks(fn, html: str, *args, **kw) -> list[Finding]:
    return fn(parse(html), "page.html", *args, **kw)


def fails(findings):
    return [f for f in findings if f.severity == FAIL]


def warns(findings):
    return [f for f in findings if f.severity == WARN]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

class TestNumericParsing:
    @pytest.mark.parametrize("text, value, kind", [
        ("$1,234", "1234", "currency"),
        ("(1,234)", "-1234", "count"),
        ("−$1,234", "-1234", "currency"),      # U+2212, as &minus; renders
        ("-$1,234", "-1234", "currency"),
        ("$-1,234", "-1234", "currency"),
        ("1,234.56", "1234.56", "count"),
        ("12.3%", "12.3", "pct"),
        ("+45%", "45", "pct"),
        ("1.52x", "1.52", "multiple"),
        ("75 ⭐", "75", "count"),
        ("  885 ", "885", "count"),
    ])
    def test_parses(self, text, value, kind):
        p = parse_number(text)
        assert p is not None, text
        assert p.value == Decimal(value)
        assert p.kind == kind
        assert isinstance(p.value, Decimal)

    def test_k_suffix_is_approximate(self):
        p = parse_number("$51K")
        assert p.value == Decimal(51000) and p.approx

    @pytest.mark.parametrize("text", ["n/a", "—", "", "$0 direct", "Earned", "Supports all channels", "(1,234"])
    def test_labels_are_not_numbers(self, text):
        assert parse_number(text) is None

    def test_decimals(self):
        assert parse_number("0.4%").decimals == 1
        assert parse_number("75").decimals == 0


class TestDom:
    def test_rendered_text_excludes_scripts_styles_and_attributes(self):
        doc = parse(page("<p class='x' data-k='oPPortunity' style='font-size:12pt'>Phone</p>",
                         head="<style>.a{font-size:12pt}</style>", script="var pts = 1;"))
        assert doc.rendered_text() == "t Phone"

    def test_tolerates_stray_close_tags(self):
        doc = parse("<div><p>one</p></span></div><p>two")
        assert doc.rendered_text() == "one two"

    def test_ancestor_attr(self):
        doc = parse("<table data-period='2026-07'><tr><td><span id='x'>1</span></td></tr></table>")
        assert doc.by_id("x").has_ancestor_attr("data-period")

    def test_tag_counts_are_raw(self):
        assert tag_counts("<div><div></div>", {"div"}) == {"div": (2, 1)}


class TestJs:
    def test_numeric_arrays_with_null(self):
        arrs = numeric_arrays("const A = [1, 2.5, null, 4]; // c\nconst B = ['x'];")
        assert arrs == {"A": [Decimal(1), Decimal("2.5"), None, Decimal(4)]}

    def test_string_arrays(self):
        assert string_arrays("const MONTHS = ['Jan 26','Feb 26'];") == {"MONTHS": ["Jan 26", "Feb 26"]}

    def test_chart_blocks_balanced(self):
        s = "new Chart(document.getElementById('a'), {data: {x: [1, (2)]}}); new Chart(ctx, {});"
        blocks = chart_blocks(s)
        assert [b.canvas_ref for b in blocks] == ["a", None]
        assert blocks[0].source.endswith("]}})")


class TestMonths:
    @pytest.mark.parametrize("text, want", [
        ("Mar 2026", (3, 2026)), ("Mar 26", (3, 2026)), ("Aug 26*", (8, 2026)),
        ("March", (3, None)), ("Sept 2025", (9, 2025)), ("Facebook", None), ("$3,029", None),
    ])
    def test_parse_month_label(self, text, want):
        assert parse_month_label(text) == want

    def test_may_needs_context(self):
        assert find_month_mentions("this may indicate a trend") == []
        assert [m[0] for m in find_month_mentions("vs May")] == ["May"]
        assert [m[0] for m in find_month_mentions("May 2026 vs April 2026")] == ["May", "April"]
        assert [m[0] for m in find_month_mentions("in March we saw")] == ["March"]


# ---------------------------------------------------------------------------
# numbers.py
# ---------------------------------------------------------------------------

def table(rows: list[list[str]], header: list[str] | None = None, total_class: bool = False) -> str:
    h = "<thead><tr>" + "".join(f"<th>{c}</th>" for c in header) + "</tr></thead>" if header else ""
    body = ""
    for r in rows:
        cls = ' class="total"' if total_class and r is rows[-1] else ""
        body += f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
    return f"<table>{h}<tbody>{body}</tbody></table>"


class TestBreakdownTables:
    def test_clean_table_passes(self):
        html = page(table([["Google", "$150,524", "189"], ["Meta", "$90,211", "42"], ["Total", "$240,735", "231"]],
                          header=["Channel", "Spent", "Customers"]))
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_total_off_fails_with_gap(self):
        html = page(table([["Google", "$150,524", "189"], ["Meta", "$90,211", "42"], ["Total", "$240,745", "231"]],
                          header=["Channel", "Spent", "Customers"]))
        fs = checks(numbers.check_breakdown_tables, html)
        assert len(fs) == 1
        assert "gap 10" in fs[0].message and "Spent" in fs[0].message

    def test_count_column_tolerance_is_half(self):
        html = page(table([["a", "1"], ["b", "2"], ["Total", "4"]], header=["x", "n"]))
        assert len(checks(numbers.check_breakdown_tables, html)) == 1
        html = page(table([["a", "$1.40"], ["b", "$2.40"], ["Total", "$4"]], header=["x", "Spend"]))
        assert checks(numbers.check_breakdown_tables, html) == []  # within $1

    def test_negative_and_parenthesised_values_sum(self):
        html = page(table([["a", "&minus;$100"], ["b", "(200)"], ["c", "$500"], ["Total", "$200"]], header=["x", "Var"]))
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_percent_and_per_columns_are_skipped(self):
        html = page(table([["a", "50%", "$10", "$5"], ["b", "50%", "$20", "$10"], ["Total", "100%", "$30", "$7.50"]],
                          header=["x", "Share", "Spend", "Cost per customer"]))
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_na_cells_contribute_nothing(self):
        html = page(table([["a", "189"], ["Agency", "n/a"], ["Total", "189"]], header=["x", "Customers"]))
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_class_total_row_is_recognised(self):
        html = page(table([["a", "1"], ["b", "2"], ["Everything", "4"]], header=["x", "n"], total_class=True))
        assert len(checks(numbers.check_breakdown_tables, html)) == 1

    def test_data_additive_false_opts_a_column_out(self):
        html = page("<table><tr><th>x</th><th>Median</th></tr><tr><td>a</td><td>1</td></tr>"
                    "<tr><td>b</td><td>3</td></tr><tr><td>Total</td><td data-additive='false'>2</td></tr></table>")
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_subtotal_and_overall_pattern(self):
        # The Leadership shape: two sections with subtotals and an OVERALL row.
        rows = [
            ["Paid Search", "$191,561"], ["Paid Social", "$19,468"], ["Paid Subtotal", "$211,029"],
            ["Organic", "$201,683"], ["Untracked", "$367,160"], ["Earned Subtotal", "$568,843"],
            ["OVERALL — All channels", "$779,872"],
        ]
        assert checks(numbers.check_breakdown_tables, page(table(rows, header=["Channel", "M1 Revenue"]))) == []
        rows[-1] = ["OVERALL — All channels", "$1,043,816"]
        fs = checks(numbers.check_breakdown_tables, page(table(rows, header=["Channel", "M1 Revenue"])))
        assert len(fs) == 1 and "total-of-subtotals" in fs[0].message and "263,944" in fs[0].message

    def test_bad_subtotal_is_caught(self):
        rows = [["a", "10"], ["b", "20"], ["Subtotal", "31"], ["OVERALL", "31"]]
        fs = checks(numbers.check_breakdown_tables, page(table(rows, header=["x", "n"])))
        assert len(fs) == 1 and "subtotal" in fs[0].message

    def test_section_header_rows_with_colspan_are_ignored(self):
        html = page("<table><tr><th>x</th><th>n</th></tr><tr><td colspan='2'>PAID CHANNELS</td></tr>"
                    "<tr><td>a</td><td>1</td></tr><tr><td>Total</td><td>1</td></tr></table>")
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_table_without_total_row_is_not_checked(self):
        html = page(table([["a", "1"], ["b", "2"]], header=["x", "n"]))
        assert checks(numbers.check_breakdown_tables, html) == []

    def test_k_suffixed_totals_get_rounding_tolerance(self):
        html = page(table([["a", "$51K"], ["b", "$76K"], ["Total", "$128K"]], header=["x", "M1"]))
        assert checks(numbers.check_breakdown_tables, html) == []


class TestComponentLists:
    TILE = ("<div class='tile'><div class='label'>Total interactions</div><div class='value'>{total}</div>"
            "<div class='sub'>240 likes · 63 comments · 34 saves · 29 shares</div></div>")

    def test_mismatch_fails(self):
        fs = checks(numbers.check_component_lists, page(self.TILE.format(total=375)))
        assert len(fs) == 1 and "366" in fs[0].message and "375" in fs[0].message

    def test_match_passes(self):
        assert checks(numbers.check_component_lists, page(self.TILE.format(total=366))) == []

    def test_uses_preceding_number_without_a_value_element(self):
        html = page("<p>375 interactions: 240 likes · 63 comments · 34 saves · 29 shares</p>")
        assert len(checks(numbers.check_component_lists, html)) == 1


class TestMetricConsistency:
    def test_same_id_different_text_fails_across_files(self):
        a = parse(page("<span data-metric='m1_2026_07'>$51,088</span>"))
        b = parse(page("<span data-metric='m1_2026_07'>$51K</span>"))
        fs = numbers.check_metric_consistency([("a.html", a), ("b.html", b)])
        assert len(fs) == 1 and "m1_2026_07" in fs[0].message and "a.html" in fs[0].file

    def test_same_id_same_text_passes(self):
        a = parse(page("<span data-metric='x'>72</span><td data-metric='x'>72</td>"))
        assert numbers.check_metric_consistency([("a.html", a)]) == []

    def test_registry_selectors(self):
        a = parse(page("<span id='hero'>$51,088</span><td class='m1'>$51,090</td>"))
        fs = numbers.check_metric_consistency([("a.html", a)], {"m1": ["#hero", "td.m1"]})
        assert len(fs) == 1 and fs[0].check == "numbers.metric_registry"
        assert numbers.check_metric_consistency([("a.html", a)], {"m1": ["#hero"]}) == []

    def test_selector_engine(self):
        doc = parse(page("<td class='a b' data-metric='q'>1</td><span data-metric='q'>2</span>"))
        assert len(numbers.select(doc, "td.a.b")) == 1
        assert len(numbers.select(doc, "[data-metric=q]")) == 2
        assert len(numbers.select(doc, "q")) == 2      # bare string = data-metric value


class TestNullMetrics:
    @pytest.mark.parametrize("text", ["None", "$None", "NaN%", "undefined", "null", "", "  "])
    def test_unresolved_values_fail(self, text):
        assert len(checks(numbers.check_no_null_metrics, page(f"<span data-metric='x'>{text}</span>"))) == 1

    @pytest.mark.parametrize("text", ["$1,234", "n/a", "—", "0", "new"])
    def test_real_values_pass(self, text):
        assert checks(numbers.check_no_null_metrics, page(f"<span data-metric='x'>{text}</span>")) == []

    def test_elements_without_data_metric_are_not_checked(self):
        assert checks(numbers.check_no_null_metrics, page("<p>None of this matters</p>")) == []


class TestTablesAgainstArrays:
    SCRIPT = "const MONTHS = ['Jan 26','Feb 26','Mar 26'];\nconst LI_NEW_FOLLOWERS = [6, 6, {mar}];\nconst LI_IMPRESSIONS = [129, 84, 254];"
    TABLE = table([["Jan 2026", "129", "6"], ["Feb 2026", "84", "6"], ["Mar 2026", "254", "<strong>{mar}</strong> ⭐"]],
                  header=["Month", "Impressions", "New followers"])

    def test_disagreement_fails(self):
        html = page(self.TABLE.format(mar=75), script=self.SCRIPT.format(mar=74))
        fs = checks(numbers.check_tables_against_arrays, html)
        assert len(fs) == 1 and "75" in fs[0].message and "LI_NEW_FOLLOWERS[2] is 74" in fs[0].message

    def test_agreement_passes(self):
        html = page(self.TABLE.format(mar=74), script=self.SCRIPT.format(mar=74))
        assert checks(numbers.check_tables_against_arrays, html) == []

    def test_month_and_platform_tables_are_skipped(self):
        t = table([["Jan 2026", "Facebook", "$3,119"], ["Jan 2026", "Instagram", "$374"]],
                  header=["Month", "Platform", "Spend"])
        html = page(t, script="const MONTHS = ['Jan 26'];\nconst IG_SPEND = [374.14];")
        assert checks(numbers.check_tables_against_arrays, html) == []


class TestQueries:
    def test_real_queries_pass(self):
        assert numbers.check_queries() == []

    def test_missing_itemtype_clause_fails(self, tmp_path):
        src = (REPO / "src/data/queries/net_revenue_monthly.sql").read_text()
        (tmp_path / "net_revenue_monthly.sql").write_text(src.replace("AND i.itemtype   IS NOT NULL", ""))
        (tmp_path / "marketing_spend_monthly.sql").write_text((REPO / "src/data/queries/marketing_spend_monthly.sql").read_text())
        fs = numbers.check_queries(tmp_path)
        assert len(fs) == 1 and "itemtype" in fs[0].message

    def test_commented_clause_does_not_count(self, tmp_path):
        src = (REPO / "src/data/queries/net_revenue_monthly.sql").read_text()
        (tmp_path / "net_revenue_monthly.sql").write_text(src.replace("AND i.itemtype   IS NOT NULL", "-- AND i.itemtype IS NOT NULL"))
        (tmp_path / "marketing_spend_monthly.sql").write_text((REPO / "src/data/queries/marketing_spend_monthly.sql").read_text())
        assert any("itemtype" in f.message for f in numbers.check_queries(tmp_path))

    def test_unanchored_like_fails(self, tmp_path):
        (tmp_path / "net_revenue_monthly.sql").write_text((REPO / "src/data/queries/net_revenue_monthly.sql").read_text())
        src = (REPO / "src/data/queries/marketing_spend_monthly.sql").read_text()
        (tmp_path / "marketing_spend_monthly.sql").write_text(src.replace("LIKE '66212%'", "LIKE '%6212%'"))
        msgs = [f.message for f in numbers.check_queries(tmp_path)]
        assert any("unanchored" in m for m in msgs) and any("66212" in m for m in msgs)

    def test_missing_file_fails(self, tmp_path):
        assert any("missing" in f.message for f in numbers.check_queries(tmp_path))


# ---------------------------------------------------------------------------
# language.py
# ---------------------------------------------------------------------------

class TestForbiddenTerms:
    @pytest.mark.parametrize("html", [
        page("<h2>Phone capture</h2>"),                                   # caPTure
        page("<p>An opportunity, an appointment, a checkpoint.</p>"),      # oPPortunity, aPPointment, checkPOINT
        page("<p style='font-size:12pt'>fine</p>"),                        # attribute, not text
        page("<p>fine</p>", head="<style>h1{font-size:12pt}</style>"),     # style block
        page("<p>fine</p>", script="var pts = 12; var pp = 'points';"),    # script
        page("<p>Engrossing. Grossman said so.</p>"),                       # not \bgross\b
        page("<p>+22.1% vs June</p>"),
    ])
    def test_traps_do_not_fire(self, html):
        assert checks(language.check_forbidden_terms, html) == []

    @pytest.mark.parametrize("text, term", [
        ("+10.1 pts vs June", "pts"), ("up 1 pt", "pt"), ("12 points higher", "points"),
        ("a percentage point", "percentage point"), ("+3 pp", "pp"), ("gross revenue", "gross"),
        ("Gross margin", "Gross"), ("percentage points", "percentage points"),
    ])
    def test_forbidden_terms_fire(self, text, term):
        fs = checks(language.check_forbidden_terms, page(f"<p>{text}</p>"))
        assert len(fs) == 1 and repr(term) in fs[0].message

    def test_one_finding_per_text_node(self):
        fs = checks(language.check_forbidden_terms, page("<p>10 pts or 10 percentage points</p>"))
        assert len(fs) == 1


class TestCurrencyPeriod:
    def test_missing_period_fails(self):
        assert len(checks(language.check_currency_period, page("<span data-kind='currency'>$1</span>"))) == 1

    def test_period_on_self_or_ancestor_passes(self):
        html = page("<span data-kind='currency' data-period='2026-07'>$1</span>"
                    "<table data-period='Jan-Jul 2026'><tr><td data-kind='currency'>$2</td></tr></table>")
        assert checks(language.check_currency_period, html) == []

    def test_other_kinds_are_not_required_to_carry_a_period(self):
        assert checks(language.check_currency_period, page("<span data-kind='pct'>5%</span>")) == []


class TestDeltaDirection:
    def test_fall_styled_good_fails(self):
        # v1 styled a 63% fall in average deal size green.
        fs = checks(language.check_delta_direction, page("<span class='delta-good' data-delta='-63.4'>↓ 63.4%</span>"))
        assert len(fs) == 1 and "delta-bad" in fs[0].message

    def test_agreement_passes(self):
        html = page("<span class='delta-bad' data-delta='-63.4'>x</span><span class='delta-good' data-delta='+22.1'>y</span>"
                    "<span class='delta-flat' data-delta='0'>z</span>")
        assert checks(language.check_delta_direction, html) == []

    def test_higher_is_better_false_inverts(self):
        good = page("<span class='delta-good' data-delta='-48' data-higher-is-better='false'>cost fell</span>")
        bad = page("<span class='delta-good' data-delta='18.7' data-higher-is-better='false'>CPM rose</span>")
        assert checks(language.check_delta_direction, good) == []
        assert len(checks(language.check_delta_direction, bad)) == 1

    def test_zero_styled_as_good_fails(self):
        assert len(checks(language.check_delta_direction, page("<span class='delta-good' data-delta='0'>→</span>"))) == 1

    def test_missing_data_delta_is_a_warning(self):
        fs = checks(language.check_delta_direction, page("<span class='delta-good'>↑ 7%</span>"))
        assert len(fs) == 1 and fs[0].severity == WARN

    def test_non_numeric_data_delta_fails(self):
        fs = checks(language.check_delta_direction, page("<span class='delta-good' data-delta='n/a'>?</span>"))
        assert len(fs) == 1 and fs[0].severity == FAIL


class TestPercentPointDeltas:
    TILE = ("<div class='tile'><div class='label'>Phone capture</div><div class='value'>54%</div>"
            "<div class='sub'>12-mo avg 42% <span class='delta up'>{d}</span></div></div>")

    def test_raw_difference_with_percent_sign_fails(self):
        fs = checks(language.check_percent_point_deltas, page(self.TILE.format(d="+12%")))
        assert len(fs) == 1 and "54" in fs[0].message and "42" in fs[0].message and "28.6%" in fs[0].message

    def test_relative_change_passes(self):
        assert checks(language.check_percent_point_deltas, page(self.TILE.format(d="+29%"))) == []

    def test_inline_prose_form(self):
        bad = page("<p>Phone capture 45.6% → 55.7% (+10.1%)</p>")
        good = page("<p>Phone capture 45.6% → 55.7% (+22.1%)</p>")
        assert len(checks(language.check_percent_point_deltas, bad)) == 1
        assert checks(language.check_percent_point_deltas, good) == []

    def test_table_deltas_compare_within_their_own_row(self):
        # YoY compare table: 21.9% -> 23.3% is +6% relative; the 19% and 25%
        # in the row above must not be paired with it.
        html = page("<table><tr><td>Customers</td><td>19%</td><td>25%</td><td class='delta'>↑ 31%</td></tr>"
                    "<tr><td>Conversion</td><td>21.9%</td><td>23.3%</td><td class='delta good'>↑ 6%</td></tr></table>")
        assert checks(language.check_percent_point_deltas, html) == []
        html = page("<table><tr><td>Conversion</td><td>21.9%</td><td>31.9%</td><td class='delta good'>↑ 10%</td></tr></table>")
        assert len(checks(language.check_percent_point_deltas, html)) == 1

    def test_uses_the_single_delta_function(self):
        # The relative figure in the message comes from src.units.delta.
        from src.units import delta
        fs = checks(language.check_percent_point_deltas, page(self.TILE.format(d="+12%")))
        assert f"{delta(54, 42).quantize(Decimal('0.1'))}%" in fs[0].message


# ---------------------------------------------------------------------------
# narrative.py
# ---------------------------------------------------------------------------

class TestOrphanedNumbers:
    def test_typed_number_in_prose_fails(self):
        fs = checks(narrative.check_orphaned_numbers, page("<p>Revenue was $51,088 from 72 customers.</p>"))
        assert [f.evidence for f in fs] and len(fs) == 2

    def test_number_in_td_and_li_and_span_fails(self):
        html = page("<table><tr><td>72</td></tr></table><ul><li>72</li></ul><div><span>72</span></div>")
        assert len(checks(narrative.check_orphaned_numbers, html)) == 3

    def test_data_metric_and_data_claim_pass(self):
        html = page("<p>Revenue was <span data-metric='m1'>$51,088</span> from "
                    "<span data-claim='c1'>72 customers, up 7%</span>.</p>"
                    "<table data-metric='tbl'><tr><td>72</td></tr></table>")
        assert checks(narrative.check_orphaned_numbers, html) == []

    @pytest.mark.parametrize("text", [
        "in 2026", "FY2025", "Aug 5", "5 Aug", "August 5th", "2026-08-05", "2026-08", "8/5/2026",
        "M1", "M1-3", "M1–3", "Q3", "first 90 days", "12-mo avg", "6-month", "see [1]", "3rd", "10:30",
        "Jan 26 vs Feb 26",
    ])
    def test_allowlist(self, text):
        assert checks(narrative.check_orphaned_numbers, page(f"<p>{text}</p>")) == [], text

    def test_footnote_sup_and_period_labels_are_skipped(self):
        html = page("<p>x<sup>1</sup> <span data-period='2026-07'>Jul 2026</span></p>")
        assert checks(narrative.check_orphaned_numbers, html) == []

    def test_headings_are_not_prose(self):
        assert checks(narrative.check_orphaned_numbers, page("<h2>72 customers</h2>")) == []


class TestStaleMonths:
    def test_stale_month_warns(self):
        fs = checks(narrative.check_stale_months, page("<p>Latest month (May 2026) vs. previous month (April 2026).</p>"), "2026-07")
        assert len(fs) == 2 and all(f.severity == WARN for f in fs)
        assert {f.message.split("'")[1] for f in fs} == {"May", "April"}

    def test_reporting_and_prior_month_pass(self):
        html = page("<p>July 2026 vs June. Jul vs Jun.</p>")
        assert checks(narrative.check_stale_months, html, "2026-07") == []

    def test_january_prior_is_december(self):
        assert checks(narrative.check_stale_months, page("<p>vs December</p>"), "2026-01") == []

    def test_may_the_verb_is_not_a_month(self):
        assert checks(narrative.check_stale_months, page("<p>This may indicate a trend.</p>"), "2026-07") == []

    def test_data_period_labels_are_skipped(self):
        html = page("<span data-period='Jan-Jul 2026'>Jan–Jul 2026</span>")
        assert checks(narrative.check_stale_months, html, "2026-07") == []


# ---------------------------------------------------------------------------
# structural.py
# ---------------------------------------------------------------------------

class TestStructural:
    def test_tag_balance(self):
        assert structural.check_tag_balance(page("<div><p>ok</p></div><br><img src=x>"), "p.html") == []
        fs = structural.check_tag_balance(page("<div><div><p>x</p></div>"), "p.html")
        assert len(fs) == 1 and "<div>" in fs[0].message

    def test_canvas_referenced_passes(self):
        html = page("<canvas id='a'></canvas>", script="new Chart(document.getElementById('a'), {});")
        assert checks(structural.check_canvas_bindings, html) == []

    def test_canvas_unreferenced_fails(self):
        fs = checks(structural.check_canvas_bindings, page("<canvas id='a'></canvas>"))
        assert len(fs) == 1 and "never referenced" in fs[0].message

    def test_data_chart_binding_counts(self):
        assert checks(structural.check_canvas_bindings, page("<canvas id='a' data-chart='a'></canvas>")) == []

    def test_chart_on_non_canvas_fails(self):
        html = page("<div id='a'></div>", script="new Chart(document.getElementById('a'), {});")
        fs = checks(structural.check_canvas_bindings, html)
        assert any("not a <canvas>" in f.message for f in fs)

    def test_var_indirection_resolves(self):
        html = page("<div id='a'></div>", script="const ctx = document.getElementById('a'); new Chart(ctx, {});")
        assert any("not a <canvas>" in f.message for f in checks(structural.check_canvas_bindings, html))

    def test_dead_lookup_fails(self):
        html = page("<div id='a'></div>", script="document.getElementById('zz').textContent = 1;")
        fs = checks(structural.check_canvas_bindings, html)
        assert len(fs) == 1 and fs[0].check == "structural.dead_lookup"

    def test_month_pills(self):
        assert checks(structural.check_month_pills, page("<button class='month-pill' data-month='2026-07'>Jul</button>")) == []
        assert len(checks(structural.check_month_pills, page("<button class='month-pill'>Jul</button>"))) == 1

    def test_external_dependency_warns(self):
        cdn = page(head="<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js'></script>")
        local = page(head="<script src='assets/chart.umd.min.js'></script>")
        fs = checks(structural.check_external_dependencies, cdn)
        assert len(fs) == 1 and fs[0].severity == WARN and "external dependency" in fs[0].message
        assert checks(structural.check_external_dependencies, local) == []


class TestDeltaHelpers:
    JS = textwrap.dedent("""
        function ldDeltaText(curr, prev) { const diff = curr - prev; return (diff/prev*100).toFixed(1) + '%'; }
        function ldDeltaClass(curr, prev) { if (curr > prev) return 'delta up'; return 'delta flat'; }
        const smDelta = (c, p) => (c - p) / p * 100;
        function fmt$(n) { return '$' + n; }
    """)

    def test_definitions_require_a_subtraction(self):
        hs = structural.delta_helper_definitions(page(script=self.JS), "p.html")
        assert sorted(h.name for h in hs) == ["ldDeltaText", "smDelta"]   # ldDeltaClass picks a colour, no delta

    def test_more_than_one_fails_and_exactly_one_warns(self):
        hs = structural.delta_helper_definitions(page(script=self.JS), "p.html")
        fs = structural.check_delta_helpers(hs)
        assert len(fs) == 1 and fs[0].severity == FAIL and "2 client-side" in fs[0].message
        one = structural.check_delta_helpers(hs[:1])
        assert len(one) == 1 and one[0].severity == WARN
        assert structural.check_delta_helpers([]) == []


class TestChartClipping:
    def chart(self, mx, data="[2.3, 14.31, 21.08]"):
        return page("<canvas id='c'></canvas>", script=textwrap.dedent(f"""
            const RATE = {data};
            new Chart(document.getElementById('c'), {{
              type: 'bar',
              data: {{ datasets: [
                {{type: 'bar', label: 'Impressions', data: [100, 200, 300], yAxisID: 'y'}},
                {{type: 'line', label: 'Engagement rate %', data: RATE, yAxisID: 'y1'}}
              ] }},
              options: {{ scales: {{
                y: {{ beginAtZero: true, max: 400 }},
                y1: {{ beginAtZero: true, max: {mx}, position: 'right' }}
              }} }}
            }});
        """))

    def test_clipped_axis_fails(self):
        fs = checks(structural.check_chart_clipping, self.chart(10))
        assert len(fs) == 1 and "y1" in fs[0].message and "21.08" in fs[0].message and "Engagement rate %" in fs[0].message

    def test_roomy_axis_passes(self):
        assert checks(structural.check_chart_clipping, self.chart(25)) == []

    def test_inline_array_literal(self):
        html = page("<canvas id='c'></canvas>", script="new Chart(document.getElementById('c'), {data:{datasets:[{data:[1,50]}]}, options:{scales:{y:{max:10}}}});")
        assert len(checks(structural.check_chart_clipping, html)) == 1


class TestUndefinedClasses:
    CSS = "<style>.tile .delta.up{color:green}.tile .delta.down{color:red}</style>"

    def test_emitted_but_undefined_warns(self):
        js = "function ldDeltaClass(c,p){ if (c>p) return 'delta up'; return 'delta flat'; }"
        fs = checks(structural.check_undefined_classes, page(head=self.CSS, script=js))
        assert len(fs) == 1 and fs[0].severity == WARN and "'flat'" in fs[0].message

    def test_defined_passes(self):
        js = "function ldDeltaClass(c,p){ return 'delta up'; }"
        assert checks(structural.check_undefined_classes, page(head=self.CSS, script=js)) == []

    def test_prose_strings_are_not_classes(self):
        js = "const label = 'Last month'; el.className = 'delta up';"
        assert checks(structural.check_undefined_classes, page(head="<style>.month{}</style>" + self.CSS, script=js)) == []


# ---------------------------------------------------------------------------
# code.py
# ---------------------------------------------------------------------------

def _src_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for name, body in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    return root


UNITS = '''
    from decimal import Decimal
    def delta(current, previous):
        return (current - previous) / previous * Decimal(100)
'''


class TestCodeScan:
    def test_real_source_has_exactly_one_delta(self):
        scan = code.scan_source()
        assert [d.qualname for d in scan.delta_functions] == ["delta"]
        assert scan.delta_functions[0].file.endswith("units.py")
        assert code.check_code() == [], [f.message for f in code.check_code()]

    def test_drift_property_named_delta_is_not_a_delta_function(self):
        # freeze.DriftFinding.delta is a one-parameter property returning a
        # signed absolute difference; it is not the relative-change function.
        scan = code.scan_source()
        assert not any(d.file.endswith("freeze.py") for d in scan.delta_functions)

    def test_clean_tree_passes(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "data/x.py": "def share(a, b):\n    return a / b * 100\n"})
        assert code.check_code(root) == []

    def test_second_delta_function_fails(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "other.py": "def delta(a, b):\n    return a - b\n"})
        fs = code.check_code(root)
        assert any(f.check == "code.single_delta" and "found 2" in f.message for f in fs)

    def test_delta_in_wrong_module_fails(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": "X = 1\n", "other.py": UNITS})
        fs = code.check_code(root)
        assert any(f.check == "code.single_delta" for f in fs)

    def test_inline_relative_change_fails(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "r.py": '''
            def growth(cur, prev):
                return (cur - prev) / prev * 100
            def growth2(cur, prev):
                return 100 * (cur - prev) / prev
            def growth3(cur, prev):
                return (cur - prev) / abs(prev)
        '''})
        fs = [f for f in code.check_code(root) if f.check == "code.inline_delta"]
        assert sorted(f.message.split("(")[0] for f in fs) == ["growth", "growth2", "growth3"]

    def test_different_denominator_is_not_a_delta(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "r.py": "def margin(rev, cost, base):\n    return (rev - cost) / base * 100\n"})
        assert code.check_code(root) == []

    def test_subtraction_formatted_with_percent_fails(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "t.py": '''
            def direct(a, b):
                return f"+{a - b}%"
            def via_name(a, b):
                pts = a - b
                return f"{pts:.1f}% vs June"
            def fine(a, b):
                return f"{a - b} customers"
        '''})
        fs = [f for f in code.check_code(root) if f.check == "code.point_difference_formatted"]
        assert sorted(f.message.split("(")[0] for f in fs) == ["direct", "via_name"]

    def test_method_named_delta_with_two_operands_counts(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "c.py": '''
            class K:
                def delta(self, a, b):
                    return a - b
                @property
                def size(self):
                    return 1
        '''})
        assert any("found 2" in f.message for f in code.check_code(root))

    def test_property_named_delta_does_not_count(self, tmp_path):
        root = _src_tree(tmp_path, {"units.py": UNITS, "c.py": '''
            class K:
                live = 2
                frozen = 1
                @property
                def delta(self):
                    return self.live - self.frozen
        '''})
        assert code.check_code(root) == []


# ---------------------------------------------------------------------------
# gate.py
# ---------------------------------------------------------------------------

CLEAN_PAGE = page(
    body=textwrap.dedent("""
        <p class="banner">Internal use only</p>
        <div class="tile" data-period="2026-08">
          <div class="label">M1 NET revenue</div>
          <div class="value" data-metric="m1_2026_08" data-kind="currency">$51,088</div>
          <div class="delta-good" data-delta="22.1">↑ 22.1% vs Jul</div>
        </div>
        <p>Read: <span data-claim="c1">72 new customers in August, up 7% on July</span>.</p>
        <table data-period="Jan–Aug 2026">
          <thead><tr><th>Channel</th><th>Spend</th></tr></thead>
          <tbody>
            <tr><td>Google</td><td data-metric="sp_g" data-kind="currency">$21,370</td></tr>
            <tr><td>Meta</td><td data-metric="sp_m" data-kind="currency">$20,368</td></tr>
            <tr class="total"><td>Total</td><td data-metric="sp_t" data-kind="currency">$41,738</td></tr>
          </tbody>
        </table>
        <canvas id="spend"></canvas>
    """),
    head="<script src='assets/chart.umd.min.js'></script>",
    script="new Chart(document.getElementById('spend'), {data:{datasets:[{data:[21370, 20368]}]}});",
)


class TestGate:
    def test_clean_dist_passes(self, tmp_path):
        (tmp_path / "index.html").write_text(CLEAN_PAGE)
        report = run_gate(tmp_path, "2026-08")
        assert report.ok, report.console()
        assert report.warnings == [] and report.files == ["index.html"]
        assert isinstance(report, GateReport)

    def test_violations_fail_and_render(self, tmp_path):
        bad = CLEAN_PAGE.replace("$41,738", "$41,748").replace("↑ 22.1% vs Jul", "↑ 10.1 pts vs Jul")
        (tmp_path / "index.html").write_text(bad)
        report = run_gate(tmp_path, "2026-08")
        assert not report.ok
        assert {f.check for f in report.failures} >= {"numbers.breakdown_table", "language.forbidden_term"}
        md = report.to_markdown()
        assert "**FAIL**" in md and "numbers.breakdown_table" in md
        assert "VALIDATION GATE FAILED" in report.console()

    def test_cross_file_metric_consistency(self, tmp_path):
        (tmp_path / "a.html").write_text(CLEAN_PAGE)
        (tmp_path / "b.html").write_text(CLEAN_PAGE.replace("$51,088", "$51K", 1))
        report = run_gate(tmp_path, "2026-08")
        assert any(f.check == "numbers.metric_consistency" for f in report.failures)

    def test_registry_is_applied(self, tmp_path):
        (tmp_path / "a.html").write_text(CLEAN_PAGE)
        report = run_gate(tmp_path, "2026-08", {"m1": ["[data-metric=m1_2026_08]", "[data-metric=sp_g]"]})
        assert any(f.check == "numbers.metric_registry" for f in report.failures)

    def test_missing_dist_fails(self, tmp_path):
        assert not run_gate(tmp_path / "nope", "2026-08").ok

    def test_cli_exit_codes(self, tmp_path, capsys):
        (tmp_path / "index.html").write_text(CLEAN_PAGE)
        assert gate_main([str(tmp_path), "--period", "2026-08", "--quiet"]) == 0
        (tmp_path / "index.html").write_text(CLEAN_PAGE.replace("$41,738", "$1"))
        md = tmp_path / "report.md"
        assert gate_main([str(tmp_path), "--period", "2026-08", "--markdown", str(md)]) == 1
        assert "breakdown_table" in md.read_text()
        assert "VALIDATION GATE FAILED" in capsys.readouterr().out

    def test_package_exports(self):
        import src.validate as v
        assert v.run_gate is run_gate and v.Finding is Finding and v.GateReport is GateReport
