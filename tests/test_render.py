"""Render-layer tests: the registry, the environment, the components, the
executive page.

The point of the render layer is that a number cannot reach a page except
through a data-metric span. So the last test here renders the whole
executive dashboard from a synthetic registry and then scans the HTML for
any digit that is NOT inside a traced element. `bare_digits()` is a small
stand-in for the validation agent's real scanner; it exists so the render
layer is honest on its own, before that scanner lands.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jinja2 import UndefinedError

from src.render import (
    BRAND, ChartClippingError, ClaimError, EXECUTIVE, MetricRegistry, RegistryError,
    chart_spec, make_env, render,
)
from src.render.charts import ChartSpecError
from src.render.env import chart_json
from src.render.narrative import RenderedNarrative
from src.units import Count, Money, Pct, PointDifferenceError, Ratio, UndefinedDeltaError

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# bare-digit scanner (stand-in for the validation layer's real one)
# ---------------------------------------------------------------------------

# Elements whose text may legitimately contain digits. Everything else may not.
_EXEMPT_TAGS = {"script", "style", "time", "dfn"}
_EXEMPT_ATTRS = ("data-metric", "data-claim", "data-month")


class _DigitScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0            # >0 while inside an exempt element
        self.stack: list[bool] = []
        self.offenders: list[str] = []

    def handle_starttag(self, tag, attrs):
        keys = {k for k, _ in attrs}
        exempt = tag in _EXEMPT_TAGS or any(a in keys for a in _EXEMPT_ATTRS)
        if tag in ("img", "meta", "link", "br", "input"):   # void: no endtag
            return
        self.stack.append(exempt)
        if exempt:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("img", "meta", "link", "br", "input"):
            return
        if self.stack:
            if self.stack.pop():
                self.depth -= 1

    def handle_data(self, data):
        if self.depth == 0 and re.search(r"\d", data):
            self.offenders.append(data.strip())


def bare_digits(html: str) -> list[str]:
    """Text nodes containing a digit outside data-metric/data-claim/<time>/<dfn>."""
    s = _DigitScanner()
    s.feed(html)
    return s.offenders


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _money(x, period):
    return Money(Decimal(str(x)), period)


@pytest.fixture
def reg() -> MetricRegistry:
    r = MetricRegistry()
    r.register("jun26.phone_capture", Pct("45.6"), kind="pct", period="2026-06", source="hubspot")
    r.register("jul26.phone_capture", Pct("55.7"), kind="pct", period="2026-07", source="hubspot")
    r.register("jun26.avg_first_order", _money(1941, "2026-06"), kind="currency", source="netsuite")
    r.register("jul26.avg_first_order", _money(710, "2026-07"), kind="currency", source="netsuite")
    r.register("jul26.cpm", Ratio("13.78"), kind="ratio", period="2026-07", source="meta",
               higher_is_better=False)
    r.register("jun26.cpm", Ratio("11.61"), kind="ratio", period="2026-06", source="meta",
               higher_is_better=False)
    r.register("jul26.new_customers", Count(72, "2026-07"), kind="count", source="netsuite")
    return r


def executive_context(registry: MetricRegistry, *, pending: dict | None = None) -> dict:
    """A synthetic registry and context that satisfy the EXECUTIVE contract."""
    P = {"aug": "2026-08", "jul": "2026-07", "ytd26": "Jan–Aug 2026", "ytd25": "Jan–Aug 2025",
         "fy26": "FY2026", "r12": "Sep 2025–Aug 2026", "m13": "May 2026 cohort"}
    R = registry

    def cur(mid, amt, period, **kw):
        R.register(mid, _money(amt, period), kind="currency", source="netsuite:test", **kw)

    def cnt(mid, n, period, **kw):
        R.register(mid, Count(n, period), kind="count", source="netsuite:test", **kw)

    def pct(mid, v, period, **kw):
        R.register(mid, Pct(v), kind="pct", period=period, source="test", **kw)

    def rat(mid, v, period, fmt="per_dollar", **kw):
        R.register(mid, Ratio(v), kind="ratio", period=period, source="test", fmt=fmt, **kw)

    def txt(mid, s, period):
        R.register(mid, s, kind="text", period=period, source="test")

    cnt("aug26.new_customers", 72, P["aug"]); cnt("jul26.new_customers", 67, P["jul"])
    cur("aug26.m1_net", "51088.00", P["aug"]); cur("jul26.m1_net", "130063.00", P["jul"])
    cur("aug26.avg_first_order", "709.56", P["aug"]); cur("jul26.avg_first_order", "1941.24", P["jul"])
    rat("aug26.m1_return_per_dollar", "6.02", P["aug"])

    txt("m13.latest.cohort", "May 2026", P["m13"])
    cnt("m13.latest.customers", 69, P["m13"])
    cur("m13.latest.m1_net", "87841.00", P["m13"]); cur("m13.latest.first90_net", "116821.00", P["m13"])
    rat("m13.latest.multiple", "1.33", P["m13"], fmt="multiple")

    txt("r12.sources.top_channel", "Organic search", P["r12"])
    pct("r12.sources.top_share", "32.2", P["r12"]); pct("r12.sources.untracked_share", "40.8", P["r12"], higher_is_better=False)
    cnt("r12.sources.customers", 999, P["r12"]); cnt("r12.sources.untracked_customers", 334, P["r12"], higher_is_better=False)

    vl = "FY2025, published Aug 18, 2026"
    cnt("vintage.pre2018_accounts", 275, vl); pct("vintage.pre2018_share_of_accounts", "10.3", vl)
    pct("vintage.pre2018_share_of_revenue", "34.3", vl); cur("vintage.pre2018_avg_annual_net", "25574", vl)
    cur("vintage.band_2025_avg_annual_net", "2316", vl); cnt("vintage.active_accounts", 2676, vl)
    R.register_claim("vintage.basis_story", lambda: "published", render=lambda s: "Published Sage-basis figures; refresh pending.")

    cur("ytd26.spend", "135926.21", P["ytd26"], higher_is_better=False)
    cur("ytd25.spend", "301004.00", P["ytd25"], higher_is_better=False)
    rat("ytd26.roas_m1", "4.07", P["ytd26"]); rat("ytd26.roas_to_date", "7.59", P["ytd26"])
    txt("ytd26.roas_maturity", "4.3 months average customer-weighted maturity", P["ytd26"])
    pct("ytd26.repeat_share", "46.4", P["ytd26"])
    pct("ytd26.spend_share_of_revenue", "24.6", P["ytd26"], higher_is_better=False)
    cur("fy26.target", "24503231.00", P["fy26"]); cur("ytd26.m1_net", "552656.00", P["ytd26"])
    pct("fy26.target_growth", "19.0", P["fy26"])
    cur("ytd26.total_net", "14900000.00", P["ytd26"]); cur("ytd25.total_net", "13600000.00", P["ytd25"])
    cur("fy26.required_monthly", "2400807.75", P["fy26"]); cur("fy26.forecast_at_run_rate", "22300000.00", P["fy26"])
    cur("fy25.total_net", "20590950.41", "FY2025"); cur("fy26.still_needed", "9603231.00", P["fy26"])
    cur("fy26.gap_at_run_rate", "2203231.00", P["fy26"], higher_is_better=False)
    cur("ytd26.total_run_rate", "1850000.00", "May–Aug 2026"); cur("fy25.total_remaining_months", "7133049.00", "Sep–Dec 2025")

    cur("ytd25.m1_net", "612040.00", P["ytd25"])
    cnt("ytd26.new_customers", 517, P["ytd26"]); cnt("ytd25.new_customers", 640, P["ytd25"])
    rat("ytd26.return_per_dollar", "4.07", P["ytd26"]); rat("ytd25.return_per_dollar", "2.03", P["ytd25"])

    # budget table rows
    lines = [("Google Ads", "48000", "51088.10"), ("Meta", "24000", "19800.55"),
             ("Agency fees", "14400", "14177.73"), ("Trade shows", "20000", "0")]
    rows = []
    for i, (name, bud, act) in enumerate(lines):
        cur(f"ytd26.budget.line{i}", bud, P["ytd26"])
        cur(f"ytd26.actual.line{i}", act, P["ytd26"])
        cur(f"ytd26.variance.line{i}", str(Decimal(act) - Decimal(bud)), P["ytd26"], higher_is_better=False)
        rows.append({"line": name, "budget": f"ytd26.budget.line{i}", "actual": f"ytd26.actual.line{i}",
                     "variance": f"ytd26.variance.line{i}",
                     "status": "danger" if Decimal(act) > Decimal(bud) else None})
    budget_table = {
        "columns": [
            {"key": "line", "label": "Budget line", "kind": "text"},
            {"key": "budget", "label": "Approved budget", "kind": "metric", "align": "right", "total": True},
            {"key": "actual", "label": "Actual", "kind": "metric", "align": "right", "total": True},
            {"key": "variance", "label": "Variance", "kind": "metric", "align": "right", "total": True},
        ],
        "rows": rows,
    }

    # online table
    cnt("aug26.ga4_sessions", 25309, P["aug"]); cnt("3mo26.ga4_sessions", 81207, "Jun–Aug 2026")
    cnt("6mo26.ga4_sessions", 167292, "Mar–Aug 2026")
    pct("aug26.ga4_engagement", "52.4", P["aug"]); pct("3mo26.ga4_engagement", "51.7", "Jun–Aug 2026")
    pct("6mo26.ga4_engagement", "51.4", "Mar–Aug 2026")
    R.register_claim("aug26.sessions_read", lambda: "Lowest in six months; engagement held.")
    R.register_claim("aug26.engagement_read", lambda: "Steady. Fewer visitors, same quality.")
    online_table = {
        "columns": [
            {"key": "metric", "label": "Metric", "kind": "text"},
            {"key": "month", "label": "Reporting month", "kind": "metric", "align": "right"},
            {"key": "three", "label": "Last three months", "kind": "metric", "align": "right"},
            {"key": "six", "label": "Last six months", "kind": "metric", "align": "right"},
            {"key": "read", "label": "What the trend tells us", "kind": "claim"},
        ],
        "rows": [
            {"metric": "Website sessions", "month": "aug26.ga4_sessions", "three": "3mo26.ga4_sessions",
             "six": "6mo26.ga4_sessions", "read": "aug26.sessions_read"},
            {"metric": "Engagement rate", "month": "aug26.ga4_engagement", "three": "3mo26.ga4_engagement",
             "six": "6mo26.ga4_engagement", "read": "aug26.engagement_read", "status": "good"},
        ],
    }

    # claims for prose
    R.register_claim("aug26.volume_story",
                     lambda: R.get("aug26.new_customers").n,
                     assert_fn=lambda n: n > 0,
                     render=lambda n: "Volume up on the month; deal size regressed after a June outlier.")
    R.register_claim("ytd26.roas_story", lambda: R.get("ytd26.repeat_share"),
                     render=lambda p: f"Repeat revenue is {p} of what these cohorts have produced. "
                                      f"Month one alone credits marketing with about half of it.")
    R.register_claim("fy26.pace_story", lambda: R.get("fy26.forecast_at_run_rate").amount < R.get("fy26.target").amount,
                     render=lambda behind: "Behind the run rate needed; the remaining months must beat last year's."
                     if behind else "On track at the current run rate.")
    R.register_claim("r12.sources_story", lambda: "Organic search is the largest recorded source; untracked is larger still.")
    R.register_claim("fy26.on_track", lambda: R.get("fy26.forecast_at_run_rate").amount <= R.get("fy26.target").amount,
                     render=lambda behind: "Not on track at the current run rate. Closing the gap is priced on the Marketing Ops page.")

    # flags - bodies built through the registry so figures are traced
    R.register_claim("flag.phone_capture", lambda: R.get("jul26.phone_capture") if "jul26.phone_capture" in R.ids() else Pct("55.7"),
                     render=lambda p: f"Phone capture reached {p}, tied with the year-high.")
    flags = [
        {"severity": "green", "title": "Phone capture at a year-high", "body": R.c("flag.phone_capture")},
        {"severity": "amber", "title": "Top of funnel softened across every channel", "body": "Watch the next month closely."},
        {"severity": "blue", "title": "Google Ads now bids higher on new customers", "body": "Changed late in the month; the next month is the first clean read."},
    ]

    months = [{"id": "2026-06", "label": "Jun 26"}, {"id": "2026-07", "label": "Jul 26"}, {"id": "2026-08", "label": "Aug 26"}]
    d = Decimal
    charts = {
        "new_customers_12m": chart_spec("bar", ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"],
                                        [110, 115, 68, 65, 44, 59, 73, 63, 69, 67, 67, 72], emphasis_index=11, y_format="count"),
        "m1_net_12m": chart_spec("bar", ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"],
                                 [d("102088"), d("118602"), d("53948"), d("48840"), d("37328"), d("49840"), d("63240"),
                                  d("53952"), d("87841"), d("130063"), d("51088"), d("51088")], emphasis_index=11, y_format="usd"),
        "sources_customers": chart_spec("hbar", ["Untracked", "Organic search", "Paid search", "Paid social", "Referral"],
                                        [d("40.8"), d("32.2"), d("21.4"), d("4.7"), d("0.9")], y_format="pct"),
    }
    return {
        "page": {"title": "Executive dashboard", "slug": "executive",
                 "subtitle": "New customers, marketing return, and the target — all revenue NET."},
        "months": months, "active_month": "2026-08",
        "prepared": {"iso": date(2026, 9, 5).isoformat(), "label": "September 5, 2026"},
        "data_sources": ["NetSuite", "Google Ads", "Google Analytics", "HubSpot"],
        "asset_root": "/",
        "ids": {"cur": "aug26", "prev": "jul26", "ytd": "ytd26", "pytd": "ytd25", "fy": "fy26", "pfy": "fy25",
                "yy": "26", "pyy": "25"},
        "narrative": RenderedNarrative.empty("2026-08", "executive"),
        "report": {"month_label": "August 2026", "month_iso": "2026-08", "prev_month_label": "July 2026",
                   "prev_month_iso": "2026-07", "vintage_basis": "published",
                   "ytd_label": "January–August 2026", "ytd_iso": "2026-01/2026-08",
                   "prior_ytd_label": "January–August 2025", "prior_ytd_iso": "2025-01/2025-08",
                   "r12_label": "September 2025–August 2026", "r12_iso": "2025-09/2026-08"},
        "charts": charts,
        "tables": {"budget_vs_actual": budget_table, "online": online_table},
        "flags": flags,
        "pending": pending or {},
    }


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_m_carries_trace_attributes(self, reg):
        html = str(reg.m("jul26.phone_capture"))
        assert 'data-metric="jul26.phone_capture"' in html
        assert 'data-kind="pct"' in html
        assert 'data-period="2026-07"' in html
        assert "data-higher-is-better" in html
        assert ">55.7%<" in html

    def test_lower_is_better_omits_flag(self, reg):
        assert "data-higher-is-better" not in str(reg.m("jul26.cpm"))

    def test_unregistered_id_raises_helpfully(self, reg):
        with pytest.raises(KeyError) as e:
            reg.m("jul26.phone_captre")
        msg = str(e.value)
        assert "jul26.phone_captre" in msg
        assert "jul26.phone_capture" in msg          # the near miss is named
        assert isinstance(e.value, RegistryError)

    def test_get_records_access_and_unused_lists_the_rest(self, reg):
        reg.get("jul26.phone_capture")
        reg.m("jun26.cpm")
        unused = reg.unused()
        assert "jul26.phone_capture" not in unused
        assert "jun26.cpm" not in unused
        assert "jul26.new_customers" in unused
        assert "jun26.avg_first_order" in unused

    def test_kind_must_match_unit_type(self, reg):
        with pytest.raises(TypeError, match="requires a Money"):
            reg.register("x.y", Pct(5), kind="currency", period="p", source="s")
        with pytest.raises(TypeError, match="src.units types only"):
            reg.register("x.z", Decimal("5"), kind="pct", period="p", source="s")

    def test_point_difference_cannot_be_registered(self, reg):
        pp = Pct("55.7") - Pct("45.6")
        with pytest.raises(TypeError, match="percentage-point"):
            reg.register("x.pp", pp, kind="pct", period="p", source="s")

    def test_period_is_required_for_pct(self, reg):
        with pytest.raises(ValueError, match="period"):
            reg.register("x.p", Pct(5), kind="pct", source="s")

    def test_duplicate_id_refused(self, reg):
        with pytest.raises(ValueError, match="already registered"):
            reg.register("jul26.new_customers", Count(1, "p"), kind="count", source="s")

    def test_fmt_selects_a_units_formatter(self, reg):
        reg.register("x.mult", Ratio("1.52"), kind="ratio", period="p", source="s", fmt="multiple")
        assert ">1.52x<" in str(reg.m("x.mult"))
        with pytest.raises(ValueError, match="not a ratio formatter"):
            reg.register("x.bad", Ratio(1), kind="ratio", period="p", source="s", fmt="usd0")


class TestDeltaBetween:
    def test_phone_capture_is_relative_not_points(self, reg):
        d = reg.delta_between("jul26.phone_capture", "jun26.phone_capture")
        assert d.change_1dp == Decimal("22.1")
        assert d.css_class == "delta-good"
        html = str(d)
        assert 'data-delta="+22.1"' in html
        assert 'data-metric="jul26.phone_capture__delta"' in html
        assert "↑ 22.1%" in html
        assert "10.1" not in html

    def test_fall_in_deal_size_is_bad(self, reg):
        d = reg.delta_between("jul26.avg_first_order", "jun26.avg_first_order")
        assert d.css_class == "delta-bad"
        assert d.change_1dp == Decimal("-63.4")
        assert "↓ 63.4%" in str(d)

    def test_cost_rising_is_bad(self, reg):
        d = reg.delta_between("jul26.cpm", "jun26.cpm")
        assert d.css_class == "delta-bad"

    def test_records_both_accesses(self, reg):
        reg.delta_between("jul26.phone_capture", "jun26.phone_capture")
        assert "jul26.phone_capture" not in reg.unused()
        assert "jun26.phone_capture" not in reg.unused()

    def test_mixed_kinds_refused(self, reg):
        with pytest.raises(TypeError, match="kinds differ"):
            reg.delta_between("jul26.new_customers", "jul26.phone_capture")

    def test_zero_baseline_propagates(self, reg):
        reg.register("a.zero", Count(0, "p"), kind="count", source="s")
        reg.register("a.one", Count(5, "p"), kind="count", source="s")
        with pytest.raises(UndefinedDeltaError):
            reg.delta_between("a.one", "a.zero")


class TestClaims:
    def test_claim_renders_with_trace(self, reg):
        reg.register_claim("q.share", lambda: Pct("46.4"))
        html = str(reg.c("q.share"))
        assert html == '<span data-claim="q.share">46.4%</span>'

    def test_failed_assertion_raises(self, reg):
        reg.register_claim("q.bad", lambda: Decimal(-1), assert_fn=lambda v: v > 0)
        with pytest.raises(ClaimError):
            reg.c("q.bad")

    def test_claim_yielding_points_cannot_render(self, reg):
        reg.register_claim("q.pp", lambda: Pct("55.7") - Pct("45.6"))
        with pytest.raises(PointDifferenceError):
            reg.c("q.pp")

    def test_unknown_claim_raises(self, reg):
        with pytest.raises(KeyError):
            reg.c("nope")


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------

class TestEnvironment:
    def test_strict_undefined_raises(self, reg):
        env = make_env(reg)
        with pytest.raises(UndefinedError):
            env.from_string("{{ not_in_context }}").render()

    def test_missing_metric_in_template_raises(self, reg):
        env = make_env(reg)
        with pytest.raises(KeyError):
            env.from_string("{{ m('aug26.does_not_exist') }}").render()

    def test_metric_span_is_not_double_escaped(self, reg):
        env = make_env(reg)
        out = env.from_string("{{ m('jul26.new_customers') }}").render()
        assert out.startswith('<span data-metric="jul26.new_customers"')

    def test_plain_text_is_escaped(self, reg):
        env = make_env(reg)
        assert env.from_string("{{ x }}").render(x="<b>") == "&lt;b&gt;"

    def test_no_numeric_filters(self, reg):
        env = make_env(reg)
        for name in ("money", "pct", "usd", "currency", "format_number", "delta", "k"):
            assert name not in env.filters, f"numeric filter {name!r} present"
        # Jinja ships |round and |int; the rule is that no template uses them.
        for tpl in (REPO / "templates").rglob("*.html"):
            text = tpl.read_text()
            for f in ("|round", "|int", "|float", "|format("):
                assert f not in text, f"{tpl.name} formats a number in-template ({f})"

    def test_term_vocabulary_is_closed(self, reg):
        env = make_env(reg)
        assert env.from_string("{{ term('m1') }}").render() == '<dfn class="term" data-term="m1">M1</dfn>'
        with pytest.raises(KeyError):
            env.from_string("{{ term('m99') }}").render()

    def test_chart_json_writes_decimals_as_numbers(self):
        out = str(chart_json({"a": Decimal("51088.10"), "b": [Decimal("1"), "x<y"]}))
        assert '"a":51088.10' in out
        assert '"b":[1,"x\\u003cy"]' in out


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

class TestTable:
    def test_total_is_computed_from_rows(self, reg):
        for i, amt in enumerate(("100.50", "200.25", "300.00")):
            reg.register(f"t.budget.{i}", _money(amt, "Jan–Aug 2026"), kind="currency", source="s")
        env = make_env(reg)
        tpl = env.from_string(
            "{% import 'components/table.html' as tb %}"
            "{{ tb.table('bva', columns, rows, total_label='Total') }}"
        )
        html = tpl.render(
            columns=[{"key": "line", "label": "Line", "kind": "text"},
                     {"key": "budget", "label": "Budget", "kind": "metric", "align": "right", "total": True}],
            rows=[{"line": "a", "budget": "t.budget.0"}, {"line": "b", "budget": "t.budget.1"},
                  {"line": "c", "budget": "t.budget.2"}],
        )
        assert 'data-metric="bva.total.budget"' in html
        assert ">$601<" in html                       # 600.75 rounded by Money.usd0
        assert reg.get("bva.total.budget").amount == Decimal("600.75")
        assert "<tfoot>" in html

    def test_total_refuses_mixed_periods(self, reg):
        reg.register("t.a", _money(1, "2026-07"), kind="currency", source="s")
        reg.register("t.b", _money(1, "2026-08"), kind="currency", source="s")
        with pytest.raises(ValueError, match="different periods"):
            reg.total("t.total", ["t.a", "t.b"])

    def test_total_refuses_percentages(self, reg):
        with pytest.raises(TypeError, match="cannot sum"):
            reg.total("t.pct", ["jul26.phone_capture", "jun26.phone_capture"])

    def test_no_total_row_without_label(self, reg):
        env = make_env(reg)
        html = env.from_string(
            "{% import 'components/table.html' as tb %}{{ tb.table('x', columns, rows) }}"
        ).render(columns=[{"key": "n", "label": "N", "kind": "metric", "total": True}],
                 rows=[{"n": "jul26.new_customers"}])
        assert "<tfoot>" not in html


class TestFlagRow:
    def test_unknown_severity_raises(self, reg):
        env = make_env(reg)
        with pytest.raises(ValueError, match="severity"):
            env.from_string("{% import 'components/flag_row.html' as f %}{{ f.flag_row('yellow', 't', 'b') }}").render()

    def test_renders_icon_and_class(self, reg):
        env = make_env(reg)
        html = env.from_string("{% import 'components/flag_row.html' as f %}{{ f.flag_row('amber', 'T', 'B') }}").render()
        assert "flag-amber" in html and "🟡" in html


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------

class TestChartSpec:
    def test_clipping_is_refused(self):
        with pytest.raises(ChartClippingError, match="exceeds scale_max"):
            chart_spec("bar", ["a", "b"], [Decimal(10), Decimal(120)], scale_max=100)

    def test_donut_is_refused_with_direction(self):
        for k in ("donut", "doughnut", "pie"):
            with pytest.raises(NotImplementedError, match="sorted by value"):
                chart_spec(k, ["a"], [1])

    def test_single_series_has_no_legend_and_is_aqua(self):
        spec = chart_spec("bar", ["a", "b"], [1, 2], y_format="count")
        assert spec["options"]["plugins"]["legend"]["display"] is False
        assert spec["data"]["datasets"][0]["backgroundColor"] == BRAND.aqua
        assert spec["options"]["animation"] is False

    def test_one_yellow_emphasis_bar(self):
        spec = chart_spec("bar", ["a", "b", "c"], [1, 2, 3], emphasis_index=1)
        colours = spec["data"]["datasets"][0]["backgroundColor"]
        assert colours == [BRAND.aqua, BRAND.yellow, BRAND.aqua]

    def test_float_refused(self):
        with pytest.raises(TypeError, match="float"):
            chart_spec("bar", ["a"], [1.5])

    def test_series_ramp_ceiling(self):
        with pytest.raises(ChartSpecError, match="single-hue ramp"):
            chart_spec("bar", ["a"], [{"label": str(i), "values": [1]} for i in range(4)])

    def test_bad_y_format(self):
        with pytest.raises(ChartSpecError):
            chart_spec("bar", ["a"], [1], y_format="eur")


# ---------------------------------------------------------------------------
# brand assets
# ---------------------------------------------------------------------------

class TestBrandAssets:
    css = (REPO / "assets/css/brand.css").read_text()

    def test_css_tokens_match_python(self):
        for name, value in BRAND.as_css_vars().items():
            assert re.search(rf"{re.escape(name)}:\s*{re.escape(value)}\s*;", self.css), f"{name} {value} missing from brand.css"

    def test_no_dark_mode_no_animation_no_framework(self):
        assert "prefers-color-scheme" not in self.css
        assert "@keyframes" not in self.css and "transition" not in self.css
        assert "tailwind" not in self.css.lower() and "bootstrap" not in self.css.lower()

    def test_inter_faces_declared_and_present(self):
        for w, f in ((400, "Inter-Regular"), (700, "Inter-Bold"), (900, "Inter-Black")):
            assert f"font-weight: {w}" in self.css and f"{f}.woff2" in self.css
            assert (REPO / "assets/fonts" / f"{f}.woff2").stat().st_size > 50_000
        assert "font-style: italic" in self.css and (REPO / "assets/fonts/Inter-Italic.woff2").exists()
        assert "font-display: swap" in self.css
        assert "SIL OPEN FONT LICENSE" in (REPO / "assets/fonts/LICENSE.txt").read_text()

    def test_tabular_numerals(self):
        assert "tabular-nums" in self.css

    def test_chartjs_vendored_and_recorded(self):
        js = (REPO / "assets/vendor/chart.umd.min.js").read_text(errors="replace")
        assert "Chart.js v4.4." in js[:200]
        versions = (REPO / "assets/vendor/VERSIONS.md").read_text()
        assert "4.4.0" in versions
        import hashlib
        digest = hashlib.sha256((REPO / "assets/vendor/chart.umd.min.js").read_bytes()).hexdigest()
        assert digest in versions

    def test_base_does_not_load_a_cdn(self):
        base = (REPO / "templates/base.html").read_text()
        for host in ("cdnjs", "jsdelivr", "unpkg", "fonts.googleapis", "use.typekit"):
            assert host not in base


# ---------------------------------------------------------------------------
# the executive page
# ---------------------------------------------------------------------------

class TestExecutivePage:
    def test_contract_is_satisfied_by_fixture(self):
        r = MetricRegistry()
        ctx = executive_context(r)
        assert EXECUTIVE.for_period("2026-08").check(r.ids(), r.claim_ids(), ctx["pending"]) == []

    def test_full_render_has_no_bare_digits(self):
        r = MetricRegistry()
        ctx = executive_context(r)
        html = render("executive.html", ctx, registry=r)
        assert html.count('class="headline"') == 3, "exec summary must have exactly three cards"
        assert "Internal use only" in html
        assert 'assets/logo/vhpc-white.png"' in html
        assert 'alt="Versatile High-Performance Coatings"' in html
        for href in ("/executive", "/marketing-ops", "/sales"):
            assert f'href="{href}"' in href or f'href="{href}"' in html
        assert html.count("month-picker-row") == 1
        assert 'data-table="budget_vs_actual"' in html
        assert 'data-metric="budget_vs_actual.total.actual"' in html
        assert 'id="chart-new_customers_12m"' in html
        assert "data-pending" not in html
        offenders = bare_digits(html)
        assert offenders == [], f"untraced digits in rendered page: {offenders}"

    def test_section_order(self):
        r = MetricRegistry()
        html = render("executive.html", executive_context(r), registry=r)
        order = ["exec-summary", "Are we growing?", "Where are customers coming from?",
                 "Are we spending wisely?", "Year-over-year headline",
                 "Are we connecting with people online?", "What needs attention"]
        positions = [html.index(s) for s in order]
        assert positions == sorted(positions)

    def test_pending_sections_render_a_callout_not_a_blank(self):
        r = MetricRegistry()
        ctx = executive_context(r, pending={"online": "The analytics export for the month has not landed.",
                                            "m13_quality": "No cohort has closed its ninety-day window yet."})
        html = render("executive.html", ctx, registry=r)
        assert 'data-pending="online"' in html and 'data-pending="m13_quality"' in html
        assert 'data-table="online"' not in html
        assert bare_digits(html) == []

    def test_yoy_deltas_present_with_direction(self):
        r = MetricRegistry()
        html = render("executive.html", executive_context(r), registry=r)
        assert 'data-metric="ytd26.m1_net__delta"' in html
        # spend fell 135,926 vs 301,004 and lower spend is better -> good
        m = re.search(r'<span class="delta (delta-\w+)" data-delta="([^"]+)"( data-higher-is-better="false")? '
                      r'data-metric="ytd26.spend__delta"', html)
        assert m and m.group(1) == "delta-good" and m.group(2).startswith("-")
        # the gate checks colour against direction, so the markup must say lower-is-better
        assert m.group(3), "spend delta must carry data-higher-is-better=false"

    def test_unused_report_after_render(self):
        r = MetricRegistry()
        ctx = executive_context(r)
        render("executive.html", ctx, registry=r)
        # everything the contract lists was displayed; pending-only ids stay used because nothing is pending
        assert not any(i in r.unused() for i in EXECUTIVE.for_period("2026-08").required_metric_ids())

    def test_missing_metric_fails_the_render(self):
        r = MetricRegistry()
        ctx = executive_context(r)
        r._metrics.pop("fy26.target")
        with pytest.raises(KeyError, match="fy26.target"):
            render("executive.html", ctx, registry=r)

    def test_bare_digit_scanner_has_teeth(self):
        assert bare_digits("<p>72 customers</p>") == ["72 customers"]
        assert bare_digits('<p><span data-metric="x">72</span> customers</p>') == []
        assert bare_digits("<p><time>Aug 26</time></p>") == []
        assert bare_digits("<script>var x = 12;</script>") == []
