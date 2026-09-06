"""Registry population: every figure a page shows, registered once per page.

The build gives each page a fresh MetricRegistry. `register_core` puts in
the figures every dashboard shares (the reporting month, year-to-date, the
target, the budget, the corrections); `populate_executive`,
`populate_marketing_ops` and `populate_sales` add what their page needs and
return the template context. Nothing numeric reaches a template any other
way, and the same id renders identically on every page because it is
computed by the same line of code.

Metric ids are `<period>.<measure>`; the period half is derived from the
reporting month (`_pid('2026-08') == 'aug26'`), so templates address the
current month as `ids.cur ~ '.new_customers'` and never carry a month name.

A figure that cannot be formed is recorded in `problems` (the page is then
skipped, never rendered with a gap) or, when the whole section has a
legitimate reason to be absent, in `pending` (the page renders a labelled
"data pending" callout in that section).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .data.cohorts import Cohort, CohortSet
from .data.spend import Basis, price_ask
from .data.targets import Pace
from .freeze import DriftReport
from .ingest.common import MissingManualInput, month_label
from .periods import months_between, rolling_window, shift_month
from .units import Count, Money, Pct, Ratio, delta

if TYPE_CHECKING:  # pragma: no cover
    from .build import Inputs

__all__ = ["Core", "register_core", "populate_executive", "populate_marketing_ops", "populate_sales",
           "period_ids", "PAGES"]

RUN_RATE_MONTHS = 4          # forecast run rate = mean M1 of the last four months (May-Aug for an August report)
CONSERVATIVE_RETURN = Decimal("2.5")   # $ back per $1 assumed for the cautious version of the ask
NOISE_Z = Decimal("1.96")    # two-proportion test: below this the rep spread is noise
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_ASSETS = Path(__file__).resolve().parents[1] / "assets"

SRC_COHORTS = "netsuite:cohorts_m1"
SRC_SPEND = "netsuite:marketing_spend"
SRC_BUDGET = "manual:approved_marketing_budget"
SRC_V1_VINTAGE = ("v1 published 2026-08-18 (Sage created dates; not reproducible from NetSuite - "
                  "see acquisition_vintage.pre_2018_summary.status)")


def _d(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _pid(month: str) -> str:
    """'2026-08' -> 'aug26' - the <period> half of a metric ID."""
    return f"{_MON[int(month[5:]) - 1].lower()}{month[2:4]}"


def _short(month: str) -> str:
    return f"{_MON[int(month[5:]) - 1]} {month[2:4]}"


def _range_label(start: str, end: str) -> str:
    a, b = month_label(start), month_label(end)
    if start == end:
        return a
    return f"{a}–{b}" if start[:4] != end[:4] else f"{a.split()[0]}–{b}"


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _q1(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _clean_source(name: str) -> str:
    """'CAM37 Organic Search' -> 'Organic Search'. The CAM code is a NetSuite key, not a label."""
    parts = name.split(" ", 1)
    return parts[1] if len(parts) == 2 and parts[0].upper().startswith("CAM") and parts[0][3:].isdigit() else name


def period_ids(rm: str) -> dict[str, str]:
    """The id prefixes a template addresses, for one reporting month."""
    y, pm = int(rm[:4]), shift_month(rm, -1)
    return {
        "cur": _pid(rm), "prev": _pid(pm),
        "ytd": f"ytd{str(y)[2:]}", "pytd": f"ytd{str(y - 1)[2:]}",
        "fy": f"fy{str(y)[2:]}", "pfy": f"fy{str(y - 1)[2:]}",
        "yy": str(y)[2:], "pyy": str(y - 1)[2:],
    }


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

class R:
    """Thin wrapper: unit-typed registration with short names, plus `have`."""

    def __init__(self, reg: Any) -> None:
        self.reg = reg

    def have(self, mid: str) -> bool:
        return mid in self.reg.ids()

    def cur(self, mid: str, amount: Any, period: str, **kw: Any) -> None:
        self.reg.register(mid, Money(_d(amount), period), kind="currency", source=kw.pop("source", SRC_COHORTS), **kw)

    def cnt(self, mid: str, n: Any, period: str, **kw: Any) -> None:
        self.reg.register(mid, Count(int(n), period), kind="count", source=kw.pop("source", SRC_COHORTS), **kw)

    def pct(self, mid: str, v: Any, period: str, **kw: Any) -> None:
        self.reg.register(mid, Pct(_d(v)), kind="pct", period=period, source=kw.pop("source", "computed"), **kw)

    def rat(self, mid: str, v: Any, period: str, fmt: str = "per_dollar", **kw: Any) -> None:
        self.reg.register(mid, Ratio(_d(v)), kind="ratio", period=period, fmt=fmt,
                          source=kw.pop("source", "computed"), **kw)

    def txt(self, mid: str, s: str, period: str, **kw: Any) -> None:
        self.reg.register(mid, str(s), kind="text", period=period, source=kw.pop("source", "computed"), **kw)

    def claim(self, cid: str, expr: Callable[[], Any], render: Callable[[Any], str], assert_fn=None) -> None:
        self.reg.register_claim(cid, expr, assert_fn=assert_fn, render=render)


def _account_names(inp: "Inputs") -> dict[str, str]:
    """GL account display names recorded by ingest (BUILTIN.DF), newest wins.

    Snapshots written before account_names was recorded contribute nothing;
    the caller falls back to the bare GL code, never to a guessed name.
    """
    names: dict[str, str] = {}
    for month in inp.store.periods("marketing_spend"):
        body = inp.store.read(month, "marketing_spend").body
        for acct, name in (body.get("account_names") or {}).items():
            if name:
                names[acct] = str(name)
    return names


def _account_label(inp: "Inputs", acct: str, names: dict[str, str]) -> str:
    cfg = inp.spend.budget["accounts"].get(acct, {})
    if cfg.get("display"):
        return cfg["display"]
    if acct in names:
        return f"{names[acct]} (GL {acct}, no budget line)"
    return f"GL {acct} (no budget line)"


# ---------------------------------------------------------------------------
# Core: what every page shares
# ---------------------------------------------------------------------------

@dataclass
class Core:
    rm: str
    pm: str
    year: int
    ids: dict[str, str]
    ytd_months: list[str]
    prior_ytd_months: list[str]
    window: list[str]                        # rolling twelve closed months
    labels: dict[str, str]                   # ytd, pytd, fy, pfy, r12, month, prev_month
    true_monthly: dict[str, Money]
    cs: CohortSet | None = None              # this year's cohorts, Jan..reporting month
    pfy: CohortSet | None = None             # the whole prior year
    pace: Pace | None = None
    pending: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    charts: dict[str, dict] = field(default_factory=dict)
    tables: dict[str, dict] = field(default_factory=dict)
    report: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)      # layout data that is not a figure (bar widths, sparkline series)
    chart_spec: Callable[..., dict] = field(default=lambda *a, **k: {}, repr=False)

    def base_context(self, page: dict, data_sources: list[str], inp: "Inputs", *,
                     month_picker: bool = False) -> dict:
        """The context every page shares.

        `month_picker=True` lists the trailing twelve months so the page's
        `data-month` blocks (the executive scorecard) can be stepped back to
        any published month. Pages without such blocks pass False and the
        shell hides the picker rather than showing pills that do nothing.
        Flags carry an optional `pages` tuple; a flag for the operations
        audience never reaches the executive page.
        """
        slug = page["slug"]
        rank = {"red": 0, "amber": 1, "blue": 2, "green": 3}
        flags = [f for f in self.flags if f.get("pages") is None or slug in f["pages"]]
        flags.sort(key=lambda f: rank.get(f["severity"], 9))
        return {
            "page": page,
            "ids": self.ids,
            "months": [{"id": m, "label": _short(m)} for m in self.window] if month_picker else [],
            "active_month": self.rm,
            "logo_available": (_ASSETS / "logo" / "vhpc-white.png").exists(),
            "prepared": {"iso": inp.as_of.isoformat(),
                         "label": f"{month_label(inp.as_of.isoformat()[:7]).split()[0]} {inp.as_of.day}, {inp.as_of.year}"},
            "data_sources": data_sources,
            "asset_root": "/",
            "report": dict(self.report),
            "charts": dict(self.charts),
            "tables": dict(self.tables),
            "flags": [{k: v for k, v in f.items() if k != "pages"} for f in flags],
            "pending": dict(self.pending),
            **self.extra,
        }


def register_core(reg: Any, inp: "Inputs", chart_spec: Callable[..., dict], *,
                  drift: DriftReport | None) -> Core:
    r = R(reg)
    rm, pm, y = inp.reporting_month, inp.prev_month, inp.year
    ids = period_ids(rm)
    P, Pp, YTD, PYTD, FY, PFY = ids["cur"], ids["prev"], ids["ytd"], ids["pytd"], ids["fy"], ids["pfy"]
    ytd_months = months_between(f"{y}-01", rm)
    prior_ytd_months = months_between(f"{y - 1}-01", f"{y - 1}-{rm[5:]}")
    window = rolling_window(inp.as_of, 12)
    ytd_label, pytd_label = _range_label(ytd_months[0], rm), _range_label(prior_ytd_months[0], prior_ytd_months[-1])
    fy_label, pfy_label, r12_label = f"FY{y}", f"FY{y - 1}", _range_label(window[0], window[-1])
    core = Core(rm, pm, y, ids, ytd_months, prior_ytd_months, window,
                {"ytd": ytd_label, "pytd": pytd_label, "fy": fy_label, "pfy": pfy_label, "r12": r12_label,
                 "month": month_label(rm), "prev_month": month_label(pm)},
                inp.spend.monthly(Basis.TRUE_OPERATING), chart_spec=chart_spec)
    core.report = {
        "month_label": month_label(rm), "month_iso": rm, "prev_month_label": month_label(pm), "prev_month_iso": pm,
        "ytd_label": ytd_label, "ytd_iso": f"{ytd_months[0]}/{rm}",
        "prior_ytd_label": pytd_label, "prior_ytd_iso": f"{prior_ytd_months[0]}/{prior_ytd_months[-1]}",
        "r12_label": r12_label, "r12_iso": f"{window[0]}/{window[-1]}",
        "fy_label": fy_label, "pfy_label": pfy_label, "year": str(y), "prior_year": str(y - 1),
    }
    problems, pending = core.problems, core.pending
    true_monthly = core.true_monthly
    as_posted = inp.spend.monthly(Basis.AS_POSTED)

    # -- this month vs last ------------------------------------------------
    for month, pid in ((rm, P), (pm, Pp)):
        c = inp.cohorts.get(month)
        if c is None:
            problems.append(f"no cohorts_m1 snapshot for {month}")
            continue
        r.cnt(f"{pid}.new_customers", c.customers, month)
        r.cur(f"{pid}.m1_net", c.m1_net, month)
        r.cur(f"{pid}.avg_first_order", c.m1_net / Decimal(c.customers), month)
    if rm in inp.cohorts:
        spend_rm = true_monthly.get(rm)
        if spend_rm is None or spend_rm.amount <= 0:
            problems.append(f"no positive true-operating spend for {rm}; return per dollar undefined")
        else:
            r.rat(f"{P}.m1_return_per_dollar", inp.cohorts[rm].m1_net / spend_rm.amount, rm,
                  source="computed:cohorts_m1/marketing_spend")
    if rm in true_monthly:
        r.cur(f"{P}.spend_true", true_monthly[rm].amount, rm, higher_is_better=False, source=SRC_SPEND,
              note="true operating basis: corrections pushed back to the months they belong to; prior-year items excluded")
        r.cur(f"{P}.spend_as_posted", as_posted[rm].amount, rm, higher_is_better=False, source=SRC_SPEND,
              note="raw GL for the month, credits included where they landed")
    if "66212.0002" in inp.spend.budget["accounts"]:
        r.cur(f"{P}.budget_agency_fee", inp.spend.budget_monthly("66212.0002")[int(rm[5:]) - 1], rm,
              source=SRC_BUDGET, note="approved plan for the month; the agency bills a flat amount that follows the plan")

    # -- first-90-days, latest closed cohort (pendable) --------------------
    if inp.m13:
        latest = max(inp.m13)
        b = inp.m13[latest]
        label = f"{month_label(latest)} cohort"
        m1_live, first90 = _d(b["m1_net_revenue_live"]), _d(b["m13_net_revenue"])
        r.txt("m13.latest.cohort", month_label(latest), label, source="netsuite:cohorts_m13")
        r.cnt("m13.latest.customers", int(b["customers_m13"]), label, source="netsuite:cohorts_m13")
        r.cur("m13.latest.m1_net", m1_live, label, source="netsuite:cohorts_m13",
              note="M1 from the same live pull as the 90-day figure, so the multiple is formed from one basis")
        r.cur("m13.latest.first90_net", first90, label, source="netsuite:cohorts_m13")
        if m1_live > 0:
            r.rat("m13.latest.multiple", first90 / m1_live, label, fmt="multiple")
        else:
            pending["m13_quality"] = f"The {month_label(latest)} cohort has no month-one revenue to form a multiple against."
    else:
        pending["m13_quality"] = ("No first-ninety-days cohort has both closed its window and been pulled; "
                                  "the section returns when the next cohorts_m13 snapshot lands.")

    # -- spending wisely -----------------------------------------------------
    ytd_cohorts = [inp.cohorts[m] for m in ytd_months if m in inp.cohorts]
    missing_ytd = [m for m in ytd_months if m not in inp.cohorts]
    if missing_ytd:
        problems.append(f"cohorts_m1 missing for {missing_ytd}; year-to-date figures cannot be formed")
    ytd_spend = inp.spend.window(ytd_months[0], rm, Basis.TRUE_OPERATING, label=ytd_label)
    cs = None
    if ytd_cohorts and not missing_ytd and ytd_spend.amount > 0:
        cs = CohortSet(ytd_label, ytd_cohorts, ytd_spend, inp.as_of)
        core.cs = cs
        r.cur(f"{YTD}.spend", ytd_spend.amount, ytd_label, higher_is_better=False, source=SRC_SPEND,
              note="true operating basis")
        r.rat(f"{YTD}.roas_m1", cs.roas_m1.value, ytd_label)
        r.rat(f"{YTD}.roas_to_date", cs.roas_to_date.value, ytd_label)
        mat = _q1(cs.avg_maturity_months)
        r.txt(f"{YTD}.roas_maturity", f"{mat} months average customer-weighted maturity", ytd_label)
        r.txt(f"{YTD}.avg_maturity", f"{mat} months", ytd_label)
        r.pct(f"{YTD}.repeat_share", cs.repeat_share, ytd_label)
        r.pct(f"{YTD}.spend_share_of_revenue", ytd_spend.amount / cs.m1_net.amount * Decimal(100), ytd_label,
              higher_is_better=False)
        r.cur(f"{YTD}.m1_net", cs.m1_net.amount, ytd_label)
        r.cur(f"{YTD}.revenue_to_date", cs.revenue_to_date.amount, ytd_label,
              note="everything these cohorts have produced so far, month one included")
        r.cnt(f"{YTD}.new_customers", cs.customers, ytd_label)
        r.rat(f"{YTD}.return_per_dollar", cs.roas_m1.value, ytd_label)
        r.cur(f"{YTD}.cost_per_customer", ytd_spend.amount / Decimal(cs.customers), ytd_label, higher_is_better=False,
              source="computed:cohorts_m1/marketing_spend")
    elif ytd_spend.amount <= 0:
        problems.append(f"true-operating spend {ytd_label} is not positive")

    # -- the budget as a plan --------------------------------------------------
    if not inp.spend.budget["accounts"]:
        pending["budget"] = "The approved budget file for this year carries no account lines; plan figures are pending."
    if inp.spend.budget["accounts"]:
        approved = inp.spend.budget_window(f"{y}-01", f"{y}-12", honour_cancellations=False)
        effective = inp.spend.budget_window(f"{y}-01", f"{y}-12", honour_cancellations=True)
        ytd_eff = inp.spend.budget_window(ytd_months[0], rm, honour_cancellations=True)
        released = inp.spend.released_by_cancellation()
        B = f"budget{ids['yy']}"
        r.cur(f"{B}.annual_approved", approved.amount, fy_label, source=SRC_BUDGET)
        r.cur(f"{B}.annual_effective", effective.amount, fy_label, source=SRC_BUDGET,
              note="approved plan less lines the business cancelled")
        r.cur(f"{B}.released_by_cancellation", released.amount, fy_label, source=SRC_BUDGET)
        r.cur(f"{B}.ytd_effective", ytd_eff.amount, ytd_label, source=SRC_BUDGET)
        if cs is not None:
            variance = ytd_eff.amount - ytd_spend.amount     # positive = under the effective plan
            r.cur(f"{B}.ytd_variance_true", variance, ytd_label,
                  source="computed:approved_marketing_budget/marketing_spend",
                  note="effective plan to date minus true operating spend; positive means under plan")
            r.cur(f"{FY}.available_within_plan", released.amount + variance, fy_label,
                  note="budget released by cancellation plus the year-to-date variance to the effective plan")
            r.claim(f"{B}.vs_plan_story", lambda v=variance: v,
                    render=lambda v: (f"{Money(abs(v), ytd_label).usd0} under the effective plan" if v > 0 else
                                      f"{Money(abs(v), ytd_label).usd0} over the effective plan" if v < 0 else
                                      "on the effective plan to the dollar"))

    # -- the company target: total NET revenue -----------------------------------
    # Leadership's 19% is on TOTAL revenue (Jules, 2026-09-05). Marketing's own
    # frame (M1, revenue to date) is a subset formed by the same join; it is
    # reported alongside, never graded as if it were the target.
    pace = None
    basis = str(inp.target.get("basis", ""))
    if basis != "total_net_revenue":
        problems.append(f"target basis is {basis!r}; the build paces total_net_revenue only")
    target_amount = _d(inp.target["target_amount"])
    r.cur(f"{FY}.target", target_amount, fy_label, source=f"{SRC_BUDGET}.targets",
          note="total NET revenue, all customers; 19% over the prior year")
    r.pct(f"{FY}.target_growth", _d(inp.target["growth_over_prior_year_pct"]), fy_label, source=f"{SRC_BUDGET}.targets")
    elapsed = int(rm[5:])
    remaining = 12 - elapsed
    rem_months = [f"{y - 1}-{m:02d}" for m in range(elapsed + 1, 13)]
    run_months = months_between(shift_month(rm, -(RUN_RATE_MONTHS - 1)), rm)
    pfy_months = months_between(f"{y - 1}-01", f"{y - 1}-12")
    rt = inp.revenue_total
    missing_rt = [m for m in ytd_months + pfy_months if m not in rt]
    if missing_rt:
        pending["pace"] = (f"Pace against the total-revenue target needs monthly total NET revenue for "
                           f"{_range_label(pfy_months[0], rm)}; not yet ingested for {len(missing_rt)} month(s) "
                           f"({missing_rt[0]}..{missing_rt[-1]}). Run 'python -m src.ingest write revenue_total'. "
                           f"Nothing is estimated in its place.")
        core.report["pace_status"] = "pending"
        r.claim(f"{FY}.pace_story", lambda: True,
                render=lambda _: ("Pace against the total-revenue target is not yet measurable in this build: "
                                  "monthly total NET revenue has not been ingested. New-customer revenue is shown "
                                  "as marketing's contribution, not as the target."))
        r.claim(f"{FY}.on_track", lambda: True,
                render=lambda _: ("Pace against the target is pending the total-revenue series. The gap and what "
                                  "closing it would cost cannot be priced until it lands."))
    else:
        RT = "netsuite:revenue_total"
        ytd_total = sum((_d(rt[m]["net_revenue"]) for m in ytd_months), Decimal(0))
        pytd_total = sum((_d(rt[m]["net_revenue"]) for m in prior_ytd_months), Decimal(0))
        pfy_total = sum((_d(rt[m]["net_revenue"]) for m in pfy_months), Decimal(0))
        prior_rem = sum((_d(rt[m]["net_revenue"]) for m in rem_months), Decimal(0))
        run_rate = _mean([_d(rt[m]["net_revenue"]) for m in run_months])
        r.cur(f"{YTD}.total_net", ytd_total, ytd_label, source=RT)
        r.cur(f"{PYTD}.total_net", pytd_total, pytd_label, source=RT)
        r.cur(f"{PFY}.total_net", pfy_total, pfy_label, source=RT)
        declared = _d(inp.target.get("prior_year_actual") or 0)
        if declared and abs(declared - pfy_total) > Decimal("1"):
            problems.append(f"target file says prior-year total NET is {declared} but revenue_total snapshots sum to "
                            f"{pfy_total}; reconcile before publishing a pace figure")
        pace = Pace(Money(target_amount, fy_label), Money(ytd_total, fy_label), elapsed, remaining,
                    Money(prior_rem, fy_label), Money(run_rate, fy_label))
        core.pace = pace
        r.cur(f"{FY}.still_needed", pace.still_needed.amount, fy_label)
        if remaining:
            r.cur(f"{FY}.required_monthly", pace.required_monthly.amount, fy_label)
            r.cur(f"{PFY}.total_remaining_months", prior_rem, _range_label(rem_months[0], rem_months[-1]),
                  source=RT, note="the same remaining months last year, total NET")
        else:
            r.cur(f"{FY}.required_monthly", pace.still_needed.amount, fy_label,
                  note="year complete: this is the full-year shortfall, not a monthly figure")
        r.cur(f"{FY}.forecast_at_run_rate", pace.forecast_at_run_rate.amount, fy_label,
              note=f"run rate = mean total NET of {_short(run_months[0])}–{_short(run_months[-1])}")
        r.cur(f"{YTD}.total_run_rate", run_rate, _range_label(run_months[0], run_months[-1]), source=RT,
              note="mean monthly total NET over the last four months")
        gap = pace.gap_at_run_rate.amount
        r.cur(f"{FY}.gap_at_run_rate", gap, fy_label, higher_is_better=False,
              note="target minus forecast at run rate; positive means short of the target")
        if cs is not None:
            to_close = gap / cs.roas_to_date.value
            to_close_cons = gap / CONSERVATIVE_RETURN
            r.cur(f"{FY}.spend_to_close_at_marketing_return", to_close, fy_label, higher_is_better=False,
                  note="gap divided by this year's revenue-to-date return per marketing dollar; an average, "
                       "so it understates the marginal cost")
            r.cur(f"{FY}.spend_to_close_conservative", to_close_cons, fy_label, higher_is_better=False,
                  note=f"gap at an assumed marginal return of {Ratio(CONSERVATIVE_RETURN).per_dollar} per dollar")
            if inp.spend.budget["accounts"]:
                available = inp.spend.released_by_cancellation().amount + (ytd_eff.amount - ytd_spend.amount)
                r.cur(f"{FY}.shortfall_after_available", to_close - available, fy_label, higher_is_better=False,
                      note="positive means new money is needed beyond the approved plan")
                r.cur(f"{FY}.shortfall_after_available_conservative", to_close_cons - available, fy_label,
                      higher_is_better=False)
        core.report["pace_status"] = "behind" if not pace.on_track else "on_track"
        # Layout data for the pace bar: shares of the target, whole percent, capped at the bar.
        def _share(x: Decimal) -> int:
            return int(min(Decimal(100), x / target_amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        core.extra["pace_bar"] = {"ytd": _share(ytd_total), "forecast": _share(pace.forecast_at_run_rate.amount),
                                  "last_year": _share(pfy_total),
                                  "elapsed": int((Decimal(elapsed) / 12 * 100).quantize(Decimal(1)))}
        core.charts["total_net_yoy"] = chart_spec(
            "bar", [_MON[i] for i in range(elapsed)],
            [{"label": fy_label, "values": [_d(rt[m]["net_revenue"]) for m in ytd_months]},
             {"label": pfy_label, "values": [_d(rt[m]["net_revenue"]) for m in prior_ytd_months]}],
            y_format="usd")
        behind = lambda: not pace.on_track  # noqa: E731
        r.claim(f"{FY}.pace_story", behind,
                render=lambda b: ("Behind the total-revenue target at the current run rate: the remaining months "
                                  "must beat the same months last year." if b else
                                  "On track for the total-revenue target at the current run rate."))
        r.claim(f"{FY}.on_track", behind,
                render=lambda b: ("Not on track at the current run rate. The gap and what closing it "
                                  "would cost are priced on the Marketing Ops page." if b else
                                  "On track at the current run rate; hold the plan."))

    # -- marketing's frame: new-customer revenue, prior-year remaining months ---
    if cs is not None and all(m in inp.cohorts for m in rem_months + run_months):
        r.cur(f"{YTD}.run_rate", _mean([inp.cohorts[m].m1_net for m in run_months]),
              _range_label(run_months[0], run_months[-1]), note="mean month-one NET of the last four cohort months")
        if remaining:
            r.cur(f"{PFY}.m1_remaining_months", sum((inp.cohorts[m].m1_net for m in rem_months), Decimal(0)),
                  _range_label(rem_months[0], rem_months[-1]), note="the same remaining months last year, month-one NET")

    # -- year over year: the same months last year -----------------------------
    prior_cohorts = [inp.cohorts[m] for m in prior_ytd_months if m in inp.cohorts]
    if len(prior_cohorts) == len(prior_ytd_months):
        p_m1 = sum((c.m1_net for c in prior_cohorts), Decimal(0))
        p_customers = sum(c.customers for c in prior_cohorts)
        r.cur(f"{PYTD}.m1_net", p_m1, pytd_label)
        r.cnt(f"{PYTD}.new_customers", p_customers, pytd_label)
        r.cur(f"{PYTD}.avg_first_order", p_m1 / Decimal(p_customers), pytd_label)
        if cs is not None:
            r.cur(f"{YTD}.avg_first_order", cs.m1_net.amount / Decimal(cs.customers), ytd_label)
        if inp.prior_spend is not None:
            try:
                p_spend = inp.prior_spend.window(prior_ytd_months[0], prior_ytd_months[-1],
                                                 Basis.TRUE_OPERATING, label=pytd_label)
            except ValueError:
                p_spend = None
            if p_spend is None or p_spend.amount <= 0:
                problems.append(f"prior-year spend for {pytd_label} is absent or not positive")
            else:
                r.cur(f"{PYTD}.spend", p_spend.amount, pytd_label, higher_is_better=False, source=SRC_SPEND)
                r.rat(f"{PYTD}.return_per_dollar", p_m1 / p_spend.amount, pytd_label)
                r.cur(f"{PYTD}.cost_per_customer", p_spend.amount / Decimal(p_customers), pytd_label,
                      higher_is_better=False, source="computed:cohorts_m1/marketing_spend")
                p_monthly = inp.prior_spend.monthly(Basis.TRUE_OPERATING)
                peak = max((m for m in prior_ytd_months if m in p_monthly), key=lambda m: p_monthly[m].amount)
                r.txt(f"{PYTD}.peak_spend_month", month_label(peak), pytd_label, source=SRC_SPEND)
                r.cur(f"{PYTD}.peak_spend", p_monthly[peak].amount, month_label(peak), higher_is_better=False,
                      source=SRC_SPEND)
        else:
            problems.append(f"no marketing_spend snapshots for {y - 1}: {PYTD}.spend and "
                            f"{PYTD}.return_per_dollar cannot be registered")
    else:
        problems.append(f"prior-year cohorts for {pytd_label} incomplete")

    # -- the whole prior year: the class the target is measured against --------
    pfy_months = months_between(f"{y - 1}-01", f"{y - 1}-12")
    if all(m in inp.cohorts for m in pfy_months) and inp.prior_spend is not None:
        try:
            pfy_spend = inp.prior_spend.window(pfy_months[0], pfy_months[-1], Basis.TRUE_OPERATING, label=pfy_label)
        except ValueError:
            pfy_spend = None
        if pfy_spend is not None and pfy_spend.amount > 0:
            pfy = CohortSet(pfy_label, [inp.cohorts[m] for m in pfy_months], pfy_spend, inp.as_of)
            core.pfy = pfy
            r.cur(f"{PFY}.m1_net", pfy.m1_net.amount, pfy_label, note="frozen (published) basis")
            r.cnt(f"{PFY}.new_customers", pfy.customers, pfy_label)
            r.cur(f"{PFY}.spend", pfy_spend.amount, pfy_label, higher_is_better=False, source=SRC_SPEND)
            r.cur(f"{PFY}.revenue_to_date", pfy.revenue_to_date.amount, pfy_label)
            r.rat(f"{PFY}.roas_m1", pfy.roas_m1.value, pfy_label)
            r.rat(f"{PFY}.roas_to_date", pfy.roas_to_date.value, pfy_label)
            r.rat(f"{PFY}.multiple_to_date", pfy.revenue_to_date.amount / pfy.m1_net.amount, pfy_label, fmt="multiple")
            r.txt(f"{PFY}.avg_maturity", f"{_q1(pfy.avg_maturity_months)} months", pfy_label)

    # -- rolling twelve months -------------------------------------------------
    if all(m in inp.cohorts for m in window):
        r.cnt("r12.new_customers", sum(inp.cohorts[m].customers for m in window), r12_label)
        r.cur("r12.m1_net", sum((inp.cohorts[m].m1_net for m in window), Decimal(0)), r12_label)
        labels = [_short(m) for m in window]
        core.charts["new_customers_12m"] = chart_spec("bar", labels, [Decimal(inp.cohorts[m].customers) for m in window],
                                                      emphasis_index=len(window) - 1, y_format="count")
        core.charts["m1_net_12m"] = chart_spec("bar", labels, [inp.cohorts[m].m1_net for m in window],
                                               emphasis_index=len(window) - 1, y_format="usd")
    else:
        problems.append(f"cohorts_m1 missing for part of the twelve-month window {window[0]}..{window[-1]}")

    # -- the trailing twelve, month by month --------------------------------------
    # The month picker's scorecard and the twelve-month record table. Every
    # month in the window carries the same figures the reporting month does,
    # so a reader can step back to any published month and see the number the
    # deck carried then. Frozen months never move, so it is the same number.
    spend_by_month: dict[str, Decimal] = {m: v.amount for m, v in true_monthly.items()}
    if inp.prior_spend is not None:
        for m, v in inp.prior_spend.monthly(Basis.TRUE_OPERATING).items():
            spend_by_month.setdefault(m, v.amount)
    scorecard: list[dict] = []
    record_rows: list[dict] = []
    sparks: dict[str, list[Decimal | None]] = {k: [] for k in
                                               ("new_customers", "m1_net", "avg_first_order", "m1_return_per_dollar",
                                                "spend_true", "total_net")}
    for i, m in enumerate(window):
        c = inp.cohorts.get(m)
        if c is None:
            continue                                   # already recorded as a problem above
        pid, prev = _pid(m), shift_month(m, -1)
        for month, p in ((m, pid), (prev, _pid(prev))):
            cc = inp.cohorts.get(month)
            if cc is None or r.have(f"{p}.new_customers"):
                continue
            r.cnt(f"{p}.new_customers", cc.customers, month)
            r.cur(f"{p}.m1_net", cc.m1_net, month)
            r.cur(f"{p}.avg_first_order", cc.m1_net / Decimal(cc.customers), month)
        spend_m = spend_by_month.get(m)
        has_spend = spend_m is not None and spend_m > 0
        if has_spend:
            if not r.have(f"{pid}.spend_true"):
                r.cur(f"{pid}.spend_true", spend_m, m, higher_is_better=False, source=SRC_SPEND,
                      note="true operating basis")
            if not r.have(f"{pid}.m1_return_per_dollar"):
                r.rat(f"{pid}.m1_return_per_dollar", c.m1_net / spend_m, m, source="computed:cohorts_m1/marketing_spend")
        has_total = m in inp.revenue_total
        if has_total and not r.have(f"{pid}.total_net"):
            r.cur(f"{pid}.total_net", _d(inp.revenue_total[m]["net_revenue"]), m, source="netsuite:revenue_total")
        scorecard.append({"id": m, "pid": pid, "index": i, "label": month_label(m),
                          "prev": _pid(prev) if prev in inp.cohorts else None, "prev_id": prev,
                          "prev_label": month_label(prev),
                          "has_spend": has_spend, "has_total": has_total})
        record_rows.append({"month": _short(m), "customers": f"{pid}.new_customers", "m1": f"{pid}.m1_net",
                            "avg": f"{pid}.avg_first_order", "spend": f"{pid}.spend_true" if has_spend else None,
                            "ret": f"{pid}.m1_return_per_dollar" if has_spend else None,
                            "total": f"{pid}.total_net" if has_total else None,
                            "status": "warn" if m == rm else None})
        sparks["new_customers"].append(Decimal(c.customers))
        sparks["m1_net"].append(c.m1_net)
        sparks["avg_first_order"].append(c.m1_net / Decimal(c.customers))
        sparks["m1_return_per_dollar"].append(c.m1_net / spend_m if has_spend else None)
        sparks["spend_true"].append(spend_m if has_spend else None)
        sparks["total_net"].append(_d(inp.revenue_total[m]["net_revenue"]) if has_total else None)
    core.extra["scorecard"] = scorecard
    core.extra["sparks"] = sparks
    core.tables["record_12m"] = {
        "columns": [
            {"key": "month", "label": "Month", "kind": "time"},
            {"key": "customers", "label": "New customers", "kind": "metric", "align": "right"},
            {"key": "m1", "label": "First-month revenue", "kind": "metric", "align": "right"},
            {"key": "avg", "label": "Average first order", "kind": "metric", "align": "right"},
            {"key": "spend", "label": "Marketing spend", "kind": "metric", "align": "right"},
            {"key": "ret", "label": "Return per dollar, first month", "kind": "metric", "align": "right"},
            {"key": "total", "label": "Total NET revenue", "kind": "metric", "align": "right"},
        ],
        "rows": record_rows,
    }

    # -- corrections -----------------------------------------------------------
    C = f"corr{ids['yy']}"
    for c in inp.spend.corrections:
        r.cur(f"{C}.{c['id']}", abs(_d(c["credit_amount"])), f"credit posted {month_label(c['credit_month'])}",
              higher_is_better=False, source="manual:corrections", note=c.get("kind"))

    # -- budget asks ------------------------------------------------------------
    if inp.asks is None or isinstance(inp.asks, MissingManualInput):
        pending["asks"] = "No budget asks file (data/manual/<year>/budget_asks.json) for this year."
    if inp.asks is not None and not isinstance(inp.asks, MissingManualInput):
        A = f"ask{ids['yy']}"
        a_start, a_end = str(inp.asks["_meta"]["period"]).split("..")
        a_label = _range_label(a_start, a_end)
        total = Decimal(0)
        for ask in inp.asks["asks"]:
            aid = ask["id"]
            r.txt(f"{A}.{aid}.label", ask["label"], a_label, source="manual:budget_asks")
            r.txt(f"{A}.{aid}.basis", ask["basis"], a_label, source="manual:budget_asks")
            r.txt(f"{A}.{aid}.success", ask["success_measure"], a_label, source="manual:budget_asks")
            if ask["type"] == "paid_media":
                priced = price_ask(ask["monthly_media"], int(ask["months"]), inp.spend.budget, label=a_label)
                r.cur(f"{A}.{aid}.monthly_media", ask["monthly_media"], a_label, source="manual:budget_asks")
                r.cur(f"{A}.{aid}.monthly_all_in", priced["monthly_all_in"].amount, a_label,
                      source="computed:budget_asks/approved_marketing_budget.derived_lines")
                r.cur(f"{A}.{aid}.all_in", priced["all_in"].amount, a_label,
                      source="computed:budget_asks/approved_marketing_budget.derived_lines",
                      note="media plus the agency surcharge the approved budget derives on Google + Meta")
                total += priced["all_in"].amount
            else:
                r.cur(f"{A}.{aid}.all_in", ask["amount"], a_label, source="manual:budget_asks")
                total += _d(ask["amount"])
        r.cur(f"{A}.total", total, a_label, source="computed:budget_asks")
        r.pct(f"{A}.agency_rate", Decimal("20"), fy_label, source=f"{SRC_BUDGET}.derived_lines")
        r.txt(f"{A}.period", a_label, a_label, source="manual:budget_asks")
        core.tables["asks"] = {
            "columns": [
                {"key": "ask", "label": "Ask", "kind": "metric"},
                {"key": "price", "label": "Price, all-in", "kind": "metric", "align": "right", "total": True},
                {"key": "basis", "label": "Basis", "kind": "metric"},
                {"key": "success", "label": "Success measure", "kind": "metric"},
            ],
            "rows": [{"ask": f"{A}.{a['id']}.label", "price": f"{A}.{a['id']}.all_in", "basis": f"{A}.{a['id']}.basis",
                      "success": f"{A}.{a['id']}.success", "status": None} for a in inp.asks["asks"]],
        }

    # -- retention ------------------------------------------------------------
    if inp.retention is not None:
        rb = inp.retention
        w0, w1 = str(rb["cohort_window"]).split("..")
        rl = f"{_range_label(w0, w1)} cohorts"
        S = "netsuite:retention"
        r.cnt("retention.customers", rb["customers_total"], rl, source=S)
        t2 = rb["time_to_second_order"]
        r.cnt("retention.median_days_to_second_order", int(_d(t2["median_days"])), rl, source=S,
              higher_is_better=False, note="days from first order to second, median over customers who reordered")
        r.cnt("retention.reorderers", t2["n_reorderers"], rl, source=S)
        for day in (30, 90, 180):
            r.pct(f"retention.reordered_by_day_{day}", t2[f"pct_by_day_{day}"], rl, source=S,
                  note="share of eventual second orders placed by this day")
        band_labels = {"under_400": "First order under $400", "400_2499": "First order $400–$2,499",
                       "2500_plus": "First order $2,500 and above"}
        for key, b in rb["by_band"].items():
            r.txt(f"retention.{key}.label", band_labels.get(key, key), rl, source=S)
            r.cnt(f"retention.{key}.customers", b["customers"], rl, source=S)
            r.cnt(f"retention.{key}.reordered", b["reordered"], rl, source=S)
            r.cnt(f"retention.{key}.one_and_done", b["one_and_done"], rl, source=S, higher_is_better=False)
            r.pct(f"retention.{key}.rate", b["reorder_rate_pct"], rl, source=S)

    # -- account vintage ---------------------------------------------------------
    core.report["vintage_basis"] = "pending"
    if inp.vintage_accounts is not None:
        from .data.vintage import LEGACY_BAND, load_sage, vintage_table
        sage = load_sage()
        fyv = inp.vintage_accounts["fy_prior"]
        vt = vintage_table(fyv["accounts"], sage)
        vl = str(fyv.get("window") or f"FY{y - 1}")
        SV = "computed:vintage_accounts/sage_customer_sales_history"
        legacy = next(b for b in vt["bands"] if b["band"] == LEGACY_BAND)
        newest = next((b for b in vt["bands"] if b["band"] == str(y - 1)), None)
        r.cnt("vintage.legacy_accounts", legacy["accounts"], vl, source=SV,
              note=f"acquired {LEGACY_BAND}: first Sage sales year at the report's floor")
        r.pct("vintage.legacy_share_of_accounts", legacy["share_of_accounts_pct"], vl, source=SV)
        r.pct("vintage.legacy_share_of_revenue", legacy["share_of_revenue_pct"], vl, source=SV)
        r.cur("vintage.legacy_avg_annual_net", legacy["revenue_per_account"], vl, source=SV)
        if newest and newest["accounts"]:
            r.cur("vintage.newest_avg_annual_net", newest["revenue_per_account"], vl, source=SV,
                  note=f"accounts acquired in {y - 1}")
        r.cnt("vintage.active_accounts", vt["total_accounts"], vl, source=SV)
        r.cnt("vintage.sage_dated_accounts", vt["matched_sage"], vl, source=SV,
              note="accounts whose acquisition year comes from Sage sales history rather than NetSuite creation")
        rows = []
        for b in vt["bands"]:
            key = b["band"].replace(" ", "_")
            r.txt(f"vintage.band.{key}.label", b["band"], vl, source=SV)
            r.cnt(f"vintage.band.{key}.accounts", b["accounts"], vl, source=SV)
            r.cur(f"vintage.band.{key}.net_revenue", b["net_revenue"], vl, source=SV)
            r.pct(f"vintage.band.{key}.share_of_revenue", b["share_of_revenue_pct"], vl, source=SV)
            r.cur(f"vintage.band.{key}.per_account", b["revenue_per_account"], vl, source=SV)
            rows.append({"band": f"vintage.band.{key}.label", "accounts": f"vintage.band.{key}.accounts",
                         "revenue": f"vintage.band.{key}.net_revenue", "share": f"vintage.band.{key}.share_of_revenue",
                         "per_account": f"vintage.band.{key}.per_account", "status": None})
        core.extra["vintage_bar"] = [
            {"key": b["band"].replace(" ", "_"), "legacy": b["band"] == LEGACY_BAND,
             "width": int(_d(b["share_of_revenue_pct"]).quantize(Decimal(1), rounding=ROUND_HALF_UP))}
            for b in vt["bands"]]
        core.tables["vintage_bands"] = {
            "columns": [
                {"key": "band", "label": "Acquired", "kind": "metric"},
                {"key": "accounts", "label": "Active accounts", "kind": "metric", "align": "right", "total": True},
                {"key": "revenue", "label": "NET revenue", "kind": "metric", "align": "right", "total": True},
                {"key": "share", "label": "Share of revenue", "kind": "metric", "align": "right"},
                {"key": "per_account", "label": "Per account", "kind": "metric", "align": "right"},
            ],
            "rows": rows,
        }
        r.claim("vintage.basis_story", lambda: vt["matched_sage"],
                render=lambda n: (f"Acquisition year comes from the Sage sales history for accounts Sage saw and "
                                  f"from the NetSuite creation date for the rest. The oldest band is a floor: the "
                                  f"Sage reports start in 2019, so an account selling then may be far older."))
        core.report["vintage_basis"] = "computed"
    elif inp.vintage is not None:
        pr = inp.vintage.get("published_reference_2026_08_18")
        if pr:
            vl = f"FY{y - 1}, published Aug 18, 2026"
            r.cnt("vintage.pre2018_accounts", pr["pre_2018_accounts"], vl, source=SRC_V1_VINTAGE)
            r.pct("vintage.pre2018_share_of_accounts", pr["pre_2018_share_of_accounts_pct"], vl, source=SRC_V1_VINTAGE)
            r.pct("vintage.pre2018_share_of_revenue", pr["pre_2018_share_of_revenue_pct"], vl, source=SRC_V1_VINTAGE)
            r.cur("vintage.pre2018_avg_annual_net", pr["pre_2018_avg_annual_net"], vl, source=SRC_V1_VINTAGE)
            r.cur("vintage.band_2025_avg_annual_net", pr["band_2025_revenue_per_account"], vl, source=SRC_V1_VINTAGE)
            r.cnt("vintage.active_accounts", pr["fy2025_active_accounts"], vl, source=SRC_V1_VINTAGE)
            core.report["vintage_basis"] = "published"
            r.claim("vintage.basis_story", lambda: str(inp.vintage["pre_2018_summary"]["status"]),
                    render=lambda s: ("These are the figures published in August on the Sage created-date basis. "
                                      "NetSuite cannot reproduce them because legacy accounts carry a migration "
                                      "date, not an acquisition date; they refresh when a Sage export is added "
                                      "under data/manual."))
        else:
            pending["vintage"] = "The acquisition-vintage snapshot carries no published reference to display."
    else:
        pending["vintage"] = "No acquisition_vintage snapshot has been ingested for this month."

    # -- sources: first-source attribution over twelve months --------------------
    if inp.source_mix_12mo is not None:
        sm = inp.source_mix_12mo
        total_c = int(sm["totals"]["customers"])
        untracked = int(sm["untracked_customers"])
        ranked = sorted(((n, int(s["customers"])) for n, s in sm["sources"].items() if n != "Untracked"),
                        key=lambda t: -t[1])
        S = "netsuite:source_mix_12mo"
        r.cnt("r12.sources.customers", total_c, r12_label, source=S,
              note="customers created in the window who have bought at any time since; wider than the month-one count")
        r.cnt("r12.sources.untracked_customers", untracked, r12_label, source=S, higher_is_better=False)
        r.pct("r12.sources.untracked_share", Decimal(untracked) / Decimal(total_c) * 100, r12_label,
              higher_is_better=False)
        if ranked:
            r.txt("r12.sources.top_channel", _clean_source(ranked[0][0]), r12_label, source=S)
            r.pct("r12.sources.top_share", Decimal(ranked[0][1]) / Decimal(total_c) * 100, r12_label)
        bars = [("Untracked", untracked)] + [t for t in ranked if Decimal(t[1]) / Decimal(total_c) >= Decimal("0.01")]
        bars.sort(key=lambda t: -t[1])
        core.charts["sources_customers"] = chart_spec(
            "hbar", [_clean_source(n) for n, _ in bars], [Decimal(v) for _, v in bars],
            emphasis_index=[n for n, _ in bars].index("Untracked"), y_format="count")
        share = Decimal(untracked) / Decimal(total_c) * 100
        r.claim("r12.sources_story", lambda: Pct(share),
                assert_fn=lambda p: Decimal(0) <= p.value <= Decimal(100),
                render=lambda p: (f"Untracked is the largest single bucket: {p} of new customers have no recorded "
                                  f"first source. Until that falls, channel shares describe the tracked minority."))
    else:
        pending["sources"] = ("First-source attribution has not been ingested: there is no source_mix_12mo snapshot "
                              "for this month.")
        r.claim("r12.sources_story", lambda: pending.get("sources", ""),
                render=lambda s: s or "First-source attribution is shown above.")

    # -- agency platform figures, for the record ---------------------------------
    if inp.truad is not None and not isinstance(inp.truad, MissingManualInput):
        tm = {m: v for m, v in inp.truad["months"].items() if m <= rm and m[:4] == str(y)}
        if tm:
            span = _range_label(min(tm), max(tm))
            media = sum((_d(v["total"]) for v in tm.values()), Decimal(0))
            rev = sum((_d(v["platform_revenue"]) for v in tm.values()), Decimal(0))
            S = "manual:truad_media_spend"
            r.cur("truad.media_ytd", media, span, higher_is_better=False, source=S,
                  note="media only, as the agency platform reports it")
            r.cur("truad.platform_revenue_ytd", rev, span, source=S,
                  note="ad-platform-reported conversion value. NOT NetSuite revenue; shown only to size the gap")
            r.rat("truad.platform_roas", rev / media, span, source=S)
            monthly_roas = {m: _d(v["platform_revenue"]) / _d(v["total"]) for m, v in tm.items()}
            r.rat("truad.platform_roas_min", min(monthly_roas.values()), span, source=S)
            r.rat("truad.platform_roas_max", max(monthly_roas.values()), span, source=S)
            if cs is not None:
                r.rat("truad.revenue_overstatement", rev / cs.revenue_to_date.amount, span, fmt="multiple",
                      source="computed:truad/cohorts_m1", higher_is_better=False,
                      note="platform revenue divided by what the same year's cohorts have actually produced")
                r.rat("truad.roas_overstatement", (rev / media) / cs.roas_to_date.value, span, fmt="multiple",
                      source="computed:truad/cohorts_m1/marketing_spend", higher_is_better=False)

    # -- claims: prose whose truth is checked when it renders ------------------
    if rm in inp.cohorts and pm in inp.cohorts:
        r.claim(f"{P}.volume_story", lambda: delta(inp.cohorts[rm].customers, inp.cohorts[pm].customers),
                render=lambda ch: ("New-customer volume rose against the month before." if ch > 0 else
                                   "New-customer volume fell against the month before." if ch < 0 else
                                   "New-customer volume matched the month before.")
                + " Month-one revenue is a floor, not an estimate.")
    if cs is not None:
        r.claim(f"{YTD}.roas_story", lambda: Pct(cs.repeat_share),
                assert_fn=lambda p: Decimal(0) <= p.value < Decimal(100),
                render=lambda p: f"Repeat revenue is {p} of what these cohorts have produced so far; "
                                 f"judging on month one alone leaves that out.")
    # -- flags -----------------------------------------------------------------
    if pace is not None and not pace.on_track:
        core.flags.append({"severity": "red", "title": "Not on pace for the full-year target",
                           "body": reg.c(f"{FY}.on_track")})
    if drift is not None and drift.findings:
        moved = {f.period for f in drift.findings}
        worst = max((abs(f.delta_pct) for f in drift.findings if f.delta_pct is not None), default=None)
        r.claim("build.drift_story", lambda: len(moved),
                render=lambda n: f"{n} published cohort month(s) read differently in the latest live "
                                 f"pull. The published figures are held; the restatement report "
                                 f"lists every difference.")
        if worst is not None:
            r.pct("build.drift_max_move", worst, f"live pull {inp.as_of.isoformat()}", higher_is_better=False,
                  source="computed:freeze.detect_drift", note="largest relative move of any frozen figure")
        core.flags.append({"severity": "amber", "title": "Frozen figures have moved in the ledger",
                           "body": reg.c("build.drift_story"), "pages": ("marketing-ops", "sales")})
    missing_manual = [d.upper() for d, v in inp.manual.items() if isinstance(v, MissingManualInput)]
    if missing_manual:
        core.flags.append({"severity": "amber", "title": "Manual inputs pending",
                           "body": f"{' and '.join(missing_manual)} exports for the month have not been added under "
                                   f"data/manual; their sections show as pending until they are.",
                           "pages": ("marketing-ops", "sales")})
    return core


# ---------------------------------------------------------------------------
# Budget versus actual (executive + marketing ops)
# ---------------------------------------------------------------------------

def _budget_table(r: R, inp: "Inputs", core: Core) -> dict:
    """Row labels go through the registry too. An account with no budget line
    has no display name in the budget file, so its label is the GL code -
    digits that must be traceable like any other figure on the page."""
    YTD, ytd_label = core.ids["ytd"], core.labels["ytd"]
    names = _account_names(inp)
    rows = []
    for row in inp.spend.budget_vs_actual(core.ytd_months[0], core.rm):
        key = row["account"].replace(".", "_")
        r.txt(f"{YTD}.line.{key}", _account_label(inp, row["account"], names), ytd_label,
              source="manual:approved_marketing_budget|netsuite:marketing_spend")
        r.cur(f"{YTD}.budget.{key}", row["budget"].amount, ytd_label, source=SRC_BUDGET)
        r.cur(f"{YTD}.actual.{key}", row["actual"].amount, ytd_label, higher_is_better=False,
              source=SRC_SPEND, note="as posted")
        r.cur(f"{YTD}.variance.{key}", row["variance"].amount, ytd_label, higher_is_better=False)
        status = "warn" if row["unbudgeted"] else ("danger" if row["variance"].amount > 0 else None)
        rows.append({"line": f"{YTD}.line.{key}", "budget": f"{YTD}.budget.{key}", "actual": f"{YTD}.actual.{key}",
                     "variance": f"{YTD}.variance.{key}", "status": status})
    # Accounts the budget file reclassifies (the pre-split Advertising catch-all
    # is Google) get a combined actual against the target's own plan, so the
    # narrative can say "Google ran X% against plan" from one traceable pair.
    by_acct = {row["account"]: row for row in inp.spend.budget_vs_actual(core.ytd_months[0], core.rm)}
    for acct, cfg in inp.spend.budget["accounts"].items():
        target = cfg.get("reclass_to")
        if target and target in by_acct:
            name = inp.spend.budget["accounts"].get(target, {}).get("display", target).split(" ")[0].lower()
            combined = by_acct[target]["actual"].amount + by_acct.get(acct, {"actual": Money(0, ytd_label)})["actual"].amount
            r.cur(f"{YTD}.{name}_combined_actual", combined, ytd_label, higher_is_better=False, source=SRC_SPEND,
                  note=f"GL {target} plus GL {acct} (posted there before the account split)")
            r.cur(f"{YTD}.{name}_budget", by_acct[target]["budget"].amount, ytd_label, source=SRC_BUDGET)
    return {
        "columns": [
            {"key": "line", "label": "Budget line", "kind": "metric"},
            {"key": "budget", "label": "Approved budget", "kind": "metric", "align": "right", "total": True},
            {"key": "actual", "label": "Actual (as posted)", "kind": "metric", "align": "right", "total": True},
            {"key": "variance", "label": "Variance", "kind": "metric", "align": "right", "total": True},
        ],
        "rows": rows,
    }


ONLINE_WINDOWS = ((1, "month"), (3, "three"), (6, "six"))


def _window_months(rm: str, n: int) -> list[str]:
    return months_between(shift_month(rm, -(n - 1)), rm)


def _online_series(inp: "Inputs") -> list[dict]:
    """The indirect brand-health series the executive page tracks, each a
    (label, domain, extractor, kind) with the month bodies it reads."""
    def li(body, key):
        return _d(body["page_statistics"][key])

    def fa(body, key):
        return _d(body[key])
    return [
        {"key": "li_impressions", "label": "LinkedIn page impressions", "domain": "linkedin", "kind": "count",
         "get": lambda b: li(b, "page_impressions"), "src": "supermetrics:linkedin"},
        {"key": "li_engagements", "label": "LinkedIn page engagements", "domain": "linkedin", "kind": "count",
         "get": lambda b: li(b, "page_engagements"), "src": "supermetrics:linkedin"},
        {"key": "fa_impressions", "label": "Meta Ads impressions", "domain": "meta_ads", "kind": "count",
         "get": lambda b: fa(b, "impressions"), "src": "supermetrics:meta_ads"},
        {"key": "fa_clicks", "label": "Meta Ads clicks", "domain": "meta_ads", "kind": "count",
         "get": lambda b: fa(b, "clicks"), "src": "supermetrics:meta_ads"},
        {"key": "fa_spend", "label": "Meta Ads spend (platform-reported)", "domain": "meta_ads", "kind": "currency",
         "get": lambda b: fa(b, "spend"), "src": "supermetrics:meta_ads", "hib": False},
        {"key": "aw_impressions", "label": "Google Ads impressions", "domain": "google_ads", "kind": "count",
         "get": lambda b: fa(b, "impressions"), "src": "supermetrics:google_ads"},
        {"key": "aw_clicks", "label": "Google Ads clicks", "domain": "google_ads", "kind": "count",
         "get": lambda b: fa(b, "clicks"), "src": "supermetrics:google_ads"},
        {"key": "aw_cost", "label": "Google Ads spend (platform-reported)", "domain": "google_ads", "kind": "currency",
         "get": lambda b: fa(b, "cost"), "src": "supermetrics:google_ads", "hib": False},
        {"key": "ga_sessions", "label": "Website sessions (versatile.net)", "domain": "ga4", "kind": "count",
         "get": lambda b: fa(b, "sessions"), "src": "supermetrics:ga4"},
        {"key": "ga_engaged", "label": "Engaged website sessions", "domain": "ga4", "kind": "count",
         "get": lambda b: fa(b, "engaged_sessions"), "src": "supermetrics:ga4"},
        {"key": "ga_new_users", "label": "New website users", "domain": "ga4", "kind": "count",
         "get": lambda b: fa(b, "new_users"), "src": "supermetrics:ga4"},
        {"key": "ig_profile_views", "label": "Instagram content views", "domain": "instagram", "kind": "count",
         "get": lambda b: fa(b, "profile_views"), "src": "supermetrics:instagram"},
        {"key": "ig_engagements", "label": "Instagram engagements (likes, comments, saves, shares)",
         "domain": "instagram", "kind": "count", "get": lambda b: fa(b, "engagements"), "src": "supermetrics:instagram"},
    ]


def _online_table(r: R, inp: "Inputs", core: Core) -> None:
    """Three windows so a single month cannot masquerade as a trend. Every
    cell is a registered figure; the reading column is a claim computed from
    the month against its six-month average."""
    rm = core.rm
    bodies = {"linkedin": inp.linkedin, "meta_ads": inp.meta_ads, "instagram": inp.instagram,
              "google_ads": inp.google_ads, "ga4": inp.ga4}
    six = _window_months(rm, 6)
    present = {d: [m for m in six if m in bodies[d]] for d in ("linkedin", "meta_ads", "google_ads", "ga4", "instagram")}
    absent = [d for d in ("linkedin", "meta_ads", "google_ads", "ga4", "instagram") if rm not in bodies[d]]
    if absent:
        core.pending["online"] = (f"Social and advertising snapshots for {core.labels['month']} have not been ingested "
                                  f"({', '.join(absent)}); see src/ingest/README.md.")
        return
    rows = []
    readings: dict[str, Decimal] = {}
    for series in _online_series(inp):
        d = series["domain"]
        if rm not in bodies[d]:
            continue
        cells = {}
        for n, key in ONLINE_WINDOWS:
            window = _window_months(rm, n)
            if not all(m in bodies[d] for m in window):
                cells[key] = None
                continue
            total = sum((series["get"](bodies[d][m]) for m in window), Decimal(0))
            label = _range_label(window[0], window[-1])
            mid = f"online.{series['key']}.{key}"
            if series["kind"] == "currency":
                r.cur(mid, total, label, higher_is_better=series.get("hib", True), source=series["src"])
            else:
                r.cnt(mid, int(total), label, source=series["src"])
            cells[key] = mid
        # reading: this month against the six-month monthly average
        if cells.get("six") is not None and cells.get("month") is not None:
            month_v = series["get"](bodies[d][rm])
            avg6 = sum((series["get"](bodies[d][m]) for m in six), Decimal(0)) / Decimal(6)
            cid = f"online.{series['key']}.read"
            hib = series.get("hib", True)
            if avg6 > 0:
                readings[series["key"]] = delta(month_v, avg6)
                r.claim(cid, lambda mv=month_v, av=avg6: delta(mv, av),
                        render=lambda ch, hib=hib: (
                            "In line with its six-month average." if abs(ch) < 10 else
                            f"{'Above' if ch > 0 else 'Below'} its six-month average by {abs(ch.quantize(Decimal('1')))}%"
                            + ("." if (ch > 0) == hib else "; a move in the wrong direction.")))
            else:
                r.claim(cid, lambda: True, render=lambda _: "No six-month baseline: the series was flat at zero.")
        else:
            cid = None
        rows.append({"metric": series["label"], "month": cells.get("month"), "three": cells.get("three"),
                     "six": cells.get("six"), "read": cid, "status": None})
    # One finding for the executive page: paid reach falling while paid spend holds.
    paid = {k: readings.get(k) for k in ("fa_impressions", "aw_clicks", "fa_spend", "aw_cost")}
    if all(v is not None for v in paid.values()):
        falling = [k for k in ("fa_impressions", "aw_clicks") if paid[k] <= -10]
        spend_flat = all(abs(paid[k]) < 10 for k in ("fa_spend", "aw_cost"))
        if falling and spend_flat:
            whole = lambda x: abs(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))  # noqa: E731
            r.claim("online.paid_reach_story", lambda: (paid["fa_impressions"], paid["aw_clicks"]),
                    assert_fn=lambda t: t[0] <= -10 or t[1] <= -10,
                    render=lambda t: (f"Meta impressions are {whole(t[0])}% and Google Ads clicks {whole(t[1])}% below "
                                      f"their six-month averages while platform spend held in line. Each dollar is "
                                      f"buying less reach than in the spring; detail is on the Marketing Ops page."))
            core.flags.append({"severity": "amber", "title": "Paid reach is falling on flat spend",
                               "body": r.reg.c("online.paid_reach_story")})
    core.tables["online"] = {
        "columns": [
            {"key": "metric", "label": "Indicator", "kind": "text"},
            {"key": "month", "label": core.labels["month"], "kind": "metric", "align": "right"},
            {"key": "three", "label": "Last three months", "kind": "metric", "align": "right"},
            {"key": "six", "label": "Last six months", "kind": "metric", "align": "right"},
            {"key": "read", "label": "Reading", "kind": "claim"},
        ],
        "rows": rows,
    }


def _register_instagram(r: R, inp: "Inputs", core: Core) -> None:
    rm, P, Pp = core.rm, core.ids["cur"], core.ids["prev"]
    body = inp.instagram.get(rm)
    if body is None:
        core.pending["instagram"] = (f"Instagram Insights for {core.labels['month']} has not been ingested; see "
                                     f"src/ingest/README.md.")
        return
    S = "supermetrics:instagram"
    r.cnt(f"{P}.ig.reach", body["reach_unique_month"], rm, source=S,
          note="unique accounts reached in the month, from an undated query; never a sum of daily reach")
    r.cnt(f"{P}.ig.profile_views", body["profile_views"], rm, source=S,
          note="the platform's account-wide content views (reels, posts, stories), not profile-page visits")
    r.cnt(f"{P}.ig.engagements", body["engagements"], rm, source=S,
          note="likes + comments + saves + shares on media published in the month")
    if body.get("reach_daily_peak") is not None:
        r.cnt(f"{P}.ig.reach_daily_peak", body["reach_daily_peak"], rm, source=S)
    if core.pm in inp.instagram:
        prev = inp.instagram[core.pm]
        r.cnt(f"{Pp}.ig.reach", prev["reach_unique_month"], core.pm, source=S)
        r.cnt(f"{Pp}.ig.profile_views", prev["profile_views"], core.pm, source=S)
    six = [m for m in _window_months(rm, 6) if m in inp.instagram]
    if len(six) >= 2:
        core.charts["ig_reach_6m"] = core_chart(core, "bar", [_short(m) for m in six],
                                                [_d(inp.instagram[m]["reach_unique_month"]) for m in six],
                                                emphasis_index=len(six) - 1, y_format="count")


def _register_meta_ads(r: R, inp: "Inputs", core: Core) -> None:
    rm, P = core.rm, core.ids["cur"]
    body = inp.meta_ads.get(rm)
    if body is None:
        core.pending["meta_ads"] = (f"Meta Ads campaign figures for {core.labels['month']} have not been ingested; "
                                    f"see src/ingest/README.md. Nothing is carried from the previous build as if it "
                                    f"were current.")
        return
    S = "supermetrics:meta_ads"
    r.cur(f"{P}.meta.spend", body["spend"], rm, higher_is_better=False, source=S,
          note="platform-reported media spend; the ledger figure is in the paid-media reconciliation")
    r.cnt(f"{P}.meta.impressions", body["impressions"], rm, source=S)
    r.cnt(f"{P}.meta.clicks", body["clicks"], rm, source=S)
    r.pct(f"{P}.meta.ctr", body["ctr_pct"], rm, source=S)
    r.rat(f"{P}.meta.cpm", body["cpm"], rm, source=S, higher_is_better=False)
    lc = body.get("lead_campaigns") or {}
    if lc.get("leads") is not None:
        r.cnt(f"{P}.meta.leads", lc["leads"], rm, source=S, note="OUTCOME_LEADS campaigns only")
        r.cur(f"{P}.meta.lead_spend", lc["spend"], rm, higher_is_better=False, source=S)
        if lc.get("cost_per_lead") is not None:
            r.cur(f"{P}.meta.cost_per_lead", lc["cost_per_lead"], rm, higher_is_better=False, source=S, fmt="usd2")
    pm = core.pm
    if pm in inp.meta_ads:
        prev = inp.meta_ads[pm]
        Pp = core.ids["prev"]
        r.cur(f"{Pp}.meta.spend", prev["spend"], pm, higher_is_better=False, source=S)
        r.cnt(f"{Pp}.meta.clicks", prev["clicks"], pm, source=S)
        r.rat(f"{Pp}.meta.cpm", prev["cpm"], pm, source=S, higher_is_better=False)
    rows = []
    for c in sorted(body["campaigns"], key=lambda c: -_d(c["spend"])):
        key = re.sub(r"[^A-Za-z0-9]+", "_", c["campaign"]).strip("_").lower()[:60]
        base = f"{P}.meta.adset.{key}"
        r.txt(f"{base}.name", c["campaign"], rm, source=S)
        r.txt(f"{base}.objective", str(c["objective"]).replace("OUTCOME_", "").title(), rm, source=S)
        r.cur(f"{base}.spend", c["spend"], rm, higher_is_better=False, source=S)
        r.cnt(f"{base}.impressions", c["impressions"], rm, source=S)
        r.cnt(f"{base}.clicks", c["clicks"], rm, source=S)
        row = {"name": f"{base}.name", "objective": f"{base}.objective", "spend": f"{base}.spend",
               "impressions": f"{base}.impressions", "clicks": f"{base}.clicks", "leads": None, "status": None}
        if c.get("judged_on_leads") and c.get("leads") is not None:
            r.cnt(f"{base}.leads", c["leads"], rm, source=S)
            row["leads"] = f"{base}.leads"
        rows.append(row)
    core.tables["meta_adsets"] = {
        "columns": [
            {"key": "name", "label": "Ad set", "kind": "metric"},
            {"key": "objective", "label": "Objective", "kind": "metric"},
            {"key": "spend", "label": "Spend", "kind": "metric", "align": "right", "total": True},
            {"key": "impressions", "label": "Impressions", "kind": "metric", "align": "right", "total": True},
            {"key": "clicks", "label": "Clicks", "kind": "metric", "align": "right", "total": True},
            {"key": "leads", "label": "Leads (leads objective only)", "kind": "metric", "align": "right"},
        ],
        "rows": rows,
    }
    six = [m for m in _window_months(rm, 6) if m in inp.meta_ads]
    if len(six) >= 2:
        core.charts["meta_spend_6m"] = core_chart(core, "bar", [_short(m) for m in six],
                                                  [_d(inp.meta_ads[m]["spend"]) for m in six],
                                                  emphasis_index=len(six) - 1, y_format="usd")


def _register_google(r: R, inp: "Inputs", core: Core) -> None:
    rm, P, Pp = core.rm, core.ids["cur"], core.ids["prev"]
    aw, ga = inp.google_ads.get(rm), inp.ga4.get(rm)
    if aw is None or ga is None:
        missing = [d for d, b in (("google_ads", aw), ("ga4", ga)) if b is None]
        core.pending["google_web"] = (f"Google Ads and website figures for {core.labels['month']} have not both been "
                                      f"ingested ({', '.join(missing)}); see src/ingest/README.md.")
        return
    SA, SG = "supermetrics:google_ads", "supermetrics:ga4"
    r.cur(f"{P}.aw.cost", aw["cost"], rm, higher_is_better=False, source=SA,
          note="platform-reported; reconciled to the ledger in the paid-media table")
    r.cnt(f"{P}.aw.impressions", aw["impressions"], rm, source=SA)
    r.cnt(f"{P}.aw.clicks", aw["clicks"], rm, source=SA)
    r.pct(f"{P}.aw.ctr", aw["ctr_pct"], rm, source=SA)
    if aw.get("avg_cpc") is not None:
        r.cur(f"{P}.aw.avg_cpc", aw["avg_cpc"], rm, higher_is_better=False, source=SA, fmt="usd2")
    r.cur(f"{P}.aw.platform_conversion_value", aw["platform_conversion_value"], rm, source=SA,
          note="Google Ads' own attribution. NOT NetSuite revenue; shown so the gap is visible")
    for t, v in sorted(aw.get("cost_by_channel_type", {}).items()):
        key = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").lower()
        r.txt(f"{P}.aw.type.{key}.label", t, rm, source=SA)
        r.cur(f"{P}.aw.type.{key}.cost", v, rm, higher_is_better=False, source=SA)
    core.tables["aw_channel_types"] = {
        "columns": [{"key": "label", "label": "Campaign type", "kind": "metric"},
                    {"key": "cost", "label": "Spend, platform-reported", "kind": "metric", "align": "right", "total": True}],
        "rows": [{"label": f"{P}.aw.type.{re.sub(r'[^A-Za-z0-9]+', '_', t).strip('_').lower()}.label",
                  "cost": f"{P}.aw.type.{re.sub(r'[^A-Za-z0-9]+', '_', t).strip('_').lower()}.cost", "status": None}
                 for t in sorted(aw.get("cost_by_channel_type", {}))],
    }
    if core.pm in inp.google_ads:
        prev = inp.google_ads[core.pm]
        r.cur(f"{Pp}.aw.cost", prev["cost"], core.pm, higher_is_better=False, source=SA)
        r.cnt(f"{Pp}.aw.clicks", prev["clicks"], core.pm, source=SA)
    r.cnt(f"{P}.ga.sessions", ga["sessions"], rm, source=SG)
    r.cnt(f"{P}.ga.engaged_sessions", ga["engaged_sessions"], rm, source=SG)
    r.pct(f"{P}.ga.engagement_rate", ga["engagement_rate_pct"], rm, source=SG,
          note="engaged sessions over sessions for the month")
    r.cnt(f"{P}.ga.new_users", ga["new_users"], rm, source=SG)
    r.cnt(f"{P}.ga.key_events", ga["key_events"], rm, source=SG,
          note="GA4 key events (its 'conversions' count), not orders")
    if core.pm in inp.ga4:
        prev = inp.ga4[core.pm]
        r.cnt(f"{Pp}.ga.sessions", prev["sessions"], core.pm, source=SG)
        r.pct(f"{Pp}.ga.engagement_rate", prev["engagement_rate_pct"], core.pm, source=SG)
    six = [m for m in _window_months(rm, 6) if m in inp.ga4]
    if len(six) >= 2:
        core.charts["ga_sessions_6m"] = core_chart(core, "bar", [_short(m) for m in six],
                                                   [_d(inp.ga4[m]["sessions"]) for m in six],
                                                   emphasis_index=len(six) - 1, y_format="count")
    six = [m for m in _window_months(rm, 6) if m in inp.google_ads]
    if len(six) >= 2:
        core.charts["aw_cost_6m"] = core_chart(core, "bar", [_short(m) for m in six],
                                               [_d(inp.google_ads[m]["cost"]) for m in six],
                                               emphasis_index=len(six) - 1, y_format="usd")


def _register_linkedin(r: R, inp: "Inputs", core: Core) -> None:
    rm, P, Pp = core.rm, core.ids["cur"], core.ids["prev"]
    body = inp.linkedin.get(rm)
    if body is None:
        core.pending["social"] = (f"LinkedIn page figures for {core.labels['month']} have not been ingested; see "
                                  f"src/ingest/README.md.")
        return
    S = "supermetrics:linkedin"
    ps = body["page_statistics"]
    r.cnt(f"{P}.li.impressions", ps["page_impressions"], rm, source=S, note="PageStatistics; breaks down by date")
    r.cnt(f"{P}.li.engagements", ps["page_engagements"], rm, source=S)
    r.pct(f"{P}.li.engagement_rate", ps["page_engagement_rate_pct"], rm, source=S,
          note="engagements over impressions for the month, not a mean of daily rates")
    if core.pm in inp.linkedin:
        pps = inp.linkedin[core.pm]["page_statistics"]
        r.cnt(f"{Pp}.li.impressions", pps["page_impressions"], core.pm, source=S)
        r.cnt(f"{Pp}.li.engagements", pps["page_engagements"], core.pm, source=S)
    six = [m for m in _window_months(rm, 6) if m in inp.linkedin]
    if len(six) >= 2:
        core.charts["li_impressions_6m"] = core_chart(core, "bar", [_short(m) for m in six],
                                                      [_d(inp.linkedin[m]["page_statistics"]["page_impressions"]) for m in six],
                                                      emphasis_index=len(six) - 1, y_format="count")


def core_chart(core: Core, *args, **kw) -> dict:
    return core.chart_spec(*args, **kw)


# ---------------------------------------------------------------------------
# Executive
# ---------------------------------------------------------------------------

def populate_executive(reg: Any, inp: "Inputs", chart_spec: Callable[..., dict], *,
                       drift: DriftReport | None) -> tuple[dict, list[str]]:
    """Register every figure the executive page asks for; return (context, problems)."""
    core = register_core(reg, inp, chart_spec, drift=drift)
    r = R(reg)
    core.tables["budget_vs_actual"] = _budget_table(r, inp, core)
    core.tables["online"] = {"columns": [], "rows": []}
    _online_table(r, inp, core)
    context = core.base_context(
        {"title": "Executive brief", "slug": "executive",
         "subtitle": "Where the company stands against the year's target, what marketing is producing, and what "
                     "needs a decision. All revenue is NET."},
        ["NetSuite (SuiteQL via MCP)", "Approved marketing budget (manual transcription)",
         "Supermetrics (Meta, Google Ads, LinkedIn, Instagram, Google Analytics)"], inp, month_picker=True)
    return context, core.problems


# ---------------------------------------------------------------------------
# Marketing Ops
# ---------------------------------------------------------------------------

_MEDIA_ACCOUNTS = ("66212.0016", "66212.0017", "66212.0020")     # Google, Meta, pre-split Advertising
_AGENCY_ACCOUNT = "66212.0002"


def _true_account(inp: "Inputs", month: str, acct: str) -> Decimal:
    """One account's posting for one month with correction credits that landed
    there removed - the same true-operating idea, per account."""
    raw = inp.spend.postings.get(month, {}).get(acct, Decimal(0))
    for c in inp.spend.corrections:
        if c.get("account") == acct and c.get("credit_month") == month:
            raw -= _d(c["credit_amount"])
    return raw


def populate_marketing_ops(reg: Any, inp: "Inputs", chart_spec: Callable[..., dict], *,
                           drift: DriftReport | None) -> tuple[dict, list[str]]:
    core = register_core(reg, inp, chart_spec, drift=drift)
    r = R(reg)
    ids, rm, y = core.ids, core.rm, core.year
    P, YTD, PYTD, PFY = ids["cur"], ids["ytd"], ids["pytd"], ids["pfy"]
    ytd_label, pytd_label = core.labels["ytd"], core.labels["pytd"]
    pending, problems = core.pending, core.problems

    # -- paid media reconciled to the ledger ------------------------------------
    if inp.truad is None or isinstance(inp.truad, MissingManualInput):
        pending["paid_media"] = ("No agency platform media file (data/manual/<year>/truad_media_spend.json) for this "
                                 "year; the reconciliation returns when it is captured.")
    else:
        months = sorted(m for m in inp.truad["months"] if m[:4] == str(y) and m <= rm)
        closed = [m for m in months if m < rm]
        rows, worst = [], None
        sums = {"platform": Decimal(0), "gl": Decimal(0), "billed": Decimal(0), "due": Decimal(0)}
        for m in months:
            pid = _pid(m)
            platform = _d(inp.truad["months"][m]["total"])
            gl = sum((_true_account(inp, m, a) for a in _MEDIA_ACCOUNTS), Decimal(0))
            billed = _true_account(inp, m, _AGENCY_ACCOUNT)
            due = platform * Decimal("0.20")
            gap = gl - platform
            r.cur(f"truad.{pid}.media", platform, m, higher_is_better=False, source="manual:truad_media_spend")
            r.cur(f"spend.{pid}.media_gl", gl, m, higher_is_better=False, source=SRC_SPEND,
                  note="Google + Meta + pre-split Advertising, correction credits removed")
            r.cur(f"recon.{pid}.gap", gap, m, note="ledger media minus platform media")
            r.cur(f"spend.{pid}.agency_billed", billed, m, higher_is_better=False, source=SRC_SPEND)
            r.cur(f"truad.{pid}.agency_due", due, m, source="computed:truad/approved_marketing_budget.derived_lines",
                  note="20% of platform media, the rate the approved budget derives")
            rows.append({"month": _short(m), "platform": f"truad.{pid}.media", "gl": f"spend.{pid}.media_gl",
                         "gap": f"recon.{pid}.gap", "billed": f"spend.{pid}.agency_billed",
                         "due": f"truad.{pid}.agency_due", "status": "warn" if m == rm else None})
            if m in closed:
                sums["platform"] += platform; sums["gl"] += gl; sums["billed"] += billed; sums["due"] += due
                if worst is None or abs(gap) > abs(worst[1]):
                    worst = (m, gap)
        core.tables["paid_media_recon"] = {
            "columns": [
                {"key": "month", "label": "Month", "kind": "time"},
                {"key": "platform", "label": "Platform media", "kind": "metric", "align": "right"},
                {"key": "gl", "label": "Ledger media", "kind": "metric", "align": "right"},
                {"key": "gap", "label": "Ledger minus platform", "kind": "metric", "align": "right"},
                {"key": "billed", "label": "Agency fee billed", "kind": "metric", "align": "right"},
                {"key": "due", "label": "Fee at 20% of media", "kind": "metric", "align": "right"},
            ],
            "rows": rows,
        }
        if closed:
            cl = _range_label(closed[0], closed[-1])
            r.cur("truad.media_closed", sums["platform"], cl, higher_is_better=False, source="manual:truad_media_spend",
                  note="calendar-closed months only; the open month is excluded because the ledger is still moving")
            r.cur("spend.media_gl_closed", sums["gl"], cl, higher_is_better=False, source=SRC_SPEND)
            r.cur("spend.agency_billed_closed", sums["billed"], cl, higher_is_better=False, source=SRC_SPEND)
            r.cur("truad.agency_due_closed", sums["due"], cl, source="computed:truad/approved_marketing_budget")
            r.cur("recon.fee_under_billed_closed", sums["due"] - sums["billed"], cl,
                  note="fee due at 20% of actual media minus fee billed; positive means under-billed")
            if worst is not None:
                r.txt("recon.worst_gap_month", month_label(worst[0]), cl, source="computed:truad/marketing_spend")
                r.cur("recon.worst_gap", worst[1], month_label(worst[0]), source="computed:truad/marketing_spend",
                      note="ledger media minus platform media in the month that disagrees most")

    # -- channels not yet ingested ----------------------------------------------
    _register_meta_ads(r, inp, core)
    _register_linkedin(r, inp, core)
    _register_instagram(r, inp, core)
    _register_google(r, inp, core)
    pending["initiatives"] = ("Initiative status is a manual input and no data/manual/<year>/initiatives.json has been "
                              "added for this month. The previous deck's table is not repeated here as if current.")
    pending["lapsed"] = ("The lapsed-accounts query has not been run this month (named accounts; confidential). "
                         "See src/ingest/README.md.")

    # -- spend detail ------------------------------------------------------------
    core.tables["budget_vs_actual"] = _budget_table(r, inp, core)

    if inp.prior_spend is not None:
        names = _account_names(inp)
        prior = inp.prior_spend.by_account(core.prior_ytd_months[0], core.prior_ytd_months[-1], reclass=True)
        current = inp.spend.by_account(core.ytd_months[0], rm, reclass=True)
        # true basis per account: put back credits that are excluded from monthly measurement
        for c in inp.spend.corrections:
            if not c.get("affects_monthly_measurement", True) and c.get("account") in current:
                current[c["account"]] -= _d(c["credit_amount"])
        rows = []
        for acct in sorted(set(prior) | set(current)):
            key = acct.replace(".", "_")
            pv, cv = prior.get(acct, Decimal(0)), current.get(acct, Decimal(0))
            if pv == 0 and cv == 0:
                continue
            r.txt(f"yoy.{key}.label", _account_label(inp, acct, names), ytd_label,
                  source="manual:approved_marketing_budget|netsuite:marketing_spend")
            r.cur(f"yoy.{key}.prior", pv, pytd_label, higher_is_better=False, source=SRC_SPEND,
                  note="as posted, pre-split Advertising folded into Google")
            r.cur(f"yoy.{key}.current", cv, ytd_label, higher_is_better=False, source=SRC_SPEND,
                  note="as posted with prior-year credits put back, pre-split Advertising folded into Google")
            rows.append({"label": f"yoy.{key}.label", "prior": f"yoy.{key}.prior", "current": f"yoy.{key}.current",
                         "change": [f"yoy.{key}.current", f"yoy.{key}.prior"] if pv else None, "status": None})
        core.tables["yoy_channel"] = {
            "columns": [
                {"key": "label", "label": "Ledger account", "kind": "metric"},
                {"key": "prior", "label": pytd_label, "kind": "metric", "align": "right", "total": True},
                {"key": "current", "label": ytd_label, "kind": "metric", "align": "right", "total": True},
                {"key": "change", "label": "Change", "kind": "delta", "align": "right"},
            ],
            "rows": rows,
        }
        cur_total = sum(current.values(), Decimal(0))
        if core.cs is not None and abs(cur_total - core.cs.spend.amount) > Decimal("0.01"):
            problems.append(f"year-on-year channel table sums to {cur_total} but true operating spend is "
                            f"{core.cs.spend.amount}; the two bases have diverged")
    else:
        pending["yoy_channel"] = f"No marketing_spend snapshots for {y - 1}; the channel comparison needs them."

    # -- cohort detail ---------------------------------------------------------------
    if inp.m13:
        closed_m13 = sorted(inp.m13)[-12:]
        rows = []
        for m in closed_m13:
            pid, b = _pid(m), inp.m13[m]
            label = f"{month_label(m)} cohort"
            m1_live, first90 = _d(b["m1_net_revenue_live"]), _d(b["m13_net_revenue"])
            r.txt(f"m13.{pid}.cohort", month_label(m), label, source="netsuite:cohorts_m13")
            r.cnt(f"m13.{pid}.customers", int(b["customers_m13"]), label, source="netsuite:cohorts_m13")
            r.cur(f"m13.{pid}.m1_net", m1_live, label, source="netsuite:cohorts_m13")
            r.cur(f"m13.{pid}.first90_net", first90, label, source="netsuite:cohorts_m13")
            row = {"cohort": f"m13.{pid}.cohort", "customers": f"m13.{pid}.customers", "m1": f"m13.{pid}.m1_net",
                   "first90": f"m13.{pid}.first90_net", "multiple": None, "status": None}
            if m1_live > 0:
                r.rat(f"m13.{pid}.multiple", first90 / m1_live, label, fmt="multiple")
                row["multiple"] = f"m13.{pid}.multiple"
            rows.append(row)
        core.tables["m13_cohorts"] = {
            "columns": [
                {"key": "cohort", "label": "Cohort", "kind": "metric"},
                {"key": "customers", "label": "Customers with a first-90-days order", "kind": "metric", "align": "right"},
                {"key": "m1", "label": "Month-one NET", "kind": "metric", "align": "right"},
                {"key": "first90", "label": "First-90-days NET", "kind": "metric", "align": "right"},
                {"key": "multiple", "label": "Multiple of month one", "kind": "metric", "align": "right"},
            ],
            "rows": rows,
        }
        core.charts["m13_first90_12"] = chart_spec("bar", [_short(m) for m in closed_m13],
                                                   [_d(inp.m13[m]["m13_net_revenue"]) for m in closed_m13],
                                                   emphasis_index=len(closed_m13) - 1, y_format="usd")

    if all(m in inp.cohorts for m in core.ytd_months):
        rows = []
        age_label = f"as of {inp.as_of.isoformat()}"
        for m in core.ytd_months:
            pid, c = _pid(m), inp.cohorts[m]
            if not r.have(f"{pid}.new_customers"):
                r.cnt(f"{pid}.new_customers", c.customers, m)
            if not r.have(f"{pid}.m1_net"):
                r.cur(f"{pid}.m1_net", c.m1_net, m)
            r.txt(f"{pid}.cohort", month_label(m), m, source=SRC_COHORTS)
            r.cnt(f"{pid}.age_months", c.maturity_months(inp.as_of), age_label, source="computed:periods")
            r.cur(f"{pid}.revenue_to_date", c.revenue_to_date, f"{month_label(m)} cohort, to date",
                  note="month one plus live repeat revenue")
            r.rat(f"{pid}.multiple", c.revenue_to_date / c.m1_net, f"{month_label(m)} cohort, to date", fmt="multiple")
            rows.append({"cohort": f"{pid}.cohort", "age": f"{pid}.age_months", "customers": f"{pid}.new_customers",
                         "m1": f"{pid}.m1_net", "to_date": f"{pid}.revenue_to_date", "multiple": f"{pid}.multiple",
                         "status": "warn" if m == rm else None})
        core.tables["cohorts_by_age"] = {
            "columns": [
                {"key": "cohort", "label": "Cohort", "kind": "metric"},
                {"key": "age", "label": "Age (months)", "kind": "metric", "align": "right"},
                {"key": "customers", "label": "New customers", "kind": "metric", "align": "right"},
                {"key": "m1", "label": "Month-one NET", "kind": "metric", "align": "right"},
                {"key": "to_date", "label": "Revenue to date", "kind": "metric", "align": "right"},
                {"key": "multiple", "label": "Multiple of month one", "kind": "metric", "align": "right"},
            ],
            "rows": rows,
        }

    if inp.retention is None:
        pending["retention"] = "No retention snapshot has been ingested for this month."
    else:
        rows = []
        for key in ("under_400", "400_2499", "2500_plus"):
            if f"retention.{key}.rate" in reg.ids():
                rows.append({"band": f"retention.{key}.label", "customers": f"retention.{key}.customers",
                             "reordered": f"retention.{key}.reordered", "one": f"retention.{key}.one_and_done",
                             "rate": f"retention.{key}.rate", "status": None})
        core.tables["retention_bands"] = {
            "columns": [
                {"key": "band", "label": "First-order size", "kind": "metric"},
                {"key": "customers", "label": "Customers", "kind": "metric", "align": "right", "total": True},
                {"key": "reordered", "label": "Reordered", "kind": "metric", "align": "right", "total": True},
                {"key": "one", "label": "One and done", "kind": "metric", "align": "right", "total": True},
                {"key": "rate", "label": "Reorder rate", "kind": "metric", "align": "right"},
            ],
            "rows": rows,
        }

    # (the budget asks table is built in register_core; both pages show it)

    context = core.base_context(
        {"title": "Marketing operations", "slug": "marketing-ops",
         "subtitle": "Channel performance reconciled to the ledger, spend detail by account, cohort and retention "
                     "detail, and the budget asks with their prices. Named accounts may appear here; this page is "
                     "behind Cloudflare Access."},
        ["NetSuite (SuiteQL via MCP)", "Approved marketing budget (manual transcription)",
         "Agency platform media spend (manual capture; media only, never its revenue)"], inp)
    return context, problems


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------

_REP_ORDER = ("alexis", "dan", "parker")


def _z_two_proportions(c1: int, n1: int, c2: int, n2: int) -> Decimal:
    p1, p2 = Decimal(c1) / Decimal(n1), Decimal(c2) / Decimal(n2)
    p = Decimal(c1 + c2) / Decimal(n1 + n2)
    var = p * (1 - p) * (Decimal(1) / Decimal(n1) + Decimal(1) / Decimal(n2))
    if var <= 0:
        return Decimal(0)
    return abs(p1 - p2) / var.sqrt()


def populate_sales(reg: Any, inp: "Inputs", chart_spec: Callable[..., dict], *,
                   drift: DriftReport | None) -> tuple[dict, list[str]]:
    core = register_core(reg, inp, chart_spec, drift=drift)
    r = R(reg)
    ids, rm, pm = core.ids, core.rm, core.pm
    P, Pp = ids["cur"], ids["prev"]
    pending, problems = core.pending, core.problems
    SQ, SR = "netsuite:lead_quality", "netsuite:lead_routing"
    flags: list[dict] = []

    lq, lq_prev = inp.lead_quality.get(rm), inp.lead_quality.get(pm)
    routing, routing_prev = inp.routing.get(rm), inp.routing.get(pm)

    # -- what landed this month ----------------------------------------------------
    if lq is None or routing is None:
        pending["pipeline"] = (f"Lead quality and routing snapshots for {core.labels['month']} have not both been "
                               f"ingested; see src/ingest/README.md.")
    else:
        for month, pid, q in ((rm, P, lq), (pm, Pp, lq_prev)):
            if q is None:
                continue
            r.cnt(f"{pid}.lead_records", q["total_records"], month, source=SQ,
                  note="customer-table records created in the month, before any purchase")
            r.cnt(f"{pid}.leads_converted", q["customers"], month, source=SQ,
                  note="records created in the month that have become customers at any time since; the executive "
                       "count is narrower - customers who bought within their first month")
            r.pct(f"{pid}.lead_conversion", _d(q["customers"]) / _d(q["total_records"]) * 100, month,
                  source="computed:lead_quality")
            r.pct(f"{pid}.phone_capture", q["phone_capture_pct"], month, source=SQ,
                  note="blended: every record created")
            if month != rm:
                continue        # the prior month is registered only where the page shows a change against it
            r.cnt(f"{pid}.leads_assigned", q["assigned_records"], month, source=SQ)
            r.pct(f"{pid}.phone_capture_assigned", q["assigned_phone_capture_pct"], month, source=SQ,
                  note="records routed to a rep only")
            r.pct(f"{pid}.email_capture", q["email_capture_pct"], month, source=SQ)
            r.cnt(f"{pid}.with_phone", q["with_phone"], month, source=SQ)
            r.cnt(f"{pid}.assigned_with_phone", q["assigned_with_phone"], month, source=SQ)

        reps = routing["reps"]
        active = [n for n in _REP_ORDER if n in reps and reps[n]["assigned"]]
        if reps.get("other", {}).get("assigned"):
            active.append("other")
        rows = []
        for n in active:
            a, c = int(reps[n]["assigned"]), int(reps[n]["converted"])
            name = "Other reps" if n == "other" else n.title()
            r.txt(f"{P}.rep.{n}.name", name, rm, source=SR)
            r.cnt(f"{P}.rep.{n}.assigned", a, rm, source=SR)
            r.cnt(f"{P}.rep.{n}.converted", c, rm, source=SR)
            r.pct(f"{P}.rep.{n}.rate", Decimal(c) / Decimal(a) * 100, rm, source="computed:lead_routing")
            change = None
            if routing_prev and routing_prev["reps"].get(n, {}).get("assigned"):
                pa, pc = int(routing_prev["reps"][n]["assigned"]), int(routing_prev["reps"][n]["converted"])
                r.pct(f"{Pp}.rep.{n}.rate", Decimal(pc) / Decimal(pa) * 100, pm, source="computed:lead_routing")
                if pc:
                    change = [f"{P}.rep.{n}.rate", f"{Pp}.rep.{n}.rate"]
            rows.append({"rep": f"{P}.rep.{n}.name", "assigned": f"{P}.rep.{n}.assigned",
                         "converted": f"{P}.rep.{n}.converted", "rate": f"{P}.rep.{n}.rate", "change": change,
                         "status": None})
        core.tables["reps"] = {
            "columns": [
                {"key": "rep", "label": "Rep", "kind": "metric"},
                {"key": "assigned", "label": "Leads received", "kind": "metric", "align": "right", "total": True},
                {"key": "converted", "label": "Became customers", "kind": "metric", "align": "right", "total": True},
                {"key": "rate", "label": "Conversion (to date)", "kind": "metric", "align": "right"},
                {"key": "change", "label": f"vs {core.labels['prev_month']}", "kind": "delta", "align": "right"},
            ],
            "rows": rows,
        }
        assigned_total = sum(int(reps[n]["assigned"]) for n in active)
        converted_total = sum(int(reps[n]["converted"]) for n in active)
        if assigned_total:
            r.pct(f"{P}.assigned_conversion", Decimal(converted_total) / Decimal(assigned_total) * 100, rm,
                  source="computed:lead_routing", note="all records routed to a rep")
        if len(active) >= 2:
            rates = sorted(((int(reps[n]["converted"]), int(reps[n]["assigned"])) for n in active),
                           key=lambda t: Decimal(t[0]) / Decimal(t[1]))
            lo, hi = rates[0], rates[-1]
            r.claim(f"{P}.rep_spread_story", lambda: _z_two_proportions(hi[0], hi[1], lo[0], lo[1]),
                    render=lambda z: ("The spread between the highest and lowest rep conversion this month is inside "
                                      "the noise band for counts this small; the table does not rank reps."
                                      if z < NOISE_Z else
                                      "The spread between the highest and lowest rep conversion this month is wider "
                                      "than small-count noise explains; worth a conversation, not a conclusion."))
        un = reps.get("unassigned", {"assigned": 0, "converted": 0})
        r.cnt(f"{P}.unassigned_records", un["assigned"], rm, source=SR, higher_is_better=False)
        r.cnt(f"{P}.unassigned_converted", un["converted"], rm, source=SR)
        un_share = Decimal(un["assigned"]) / _d(routing["total_records"]) * 100
        r.pct(f"{P}.unassigned_share", un_share, rm, source="computed:lead_routing", higher_is_better=False)
        r.claim(f"{P}.unassigned_story", lambda: Pct(un_share),
                assert_fn=lambda p_: Decimal(0) <= p_.value <= Decimal(100),
                render=lambda p_: (f"{p_} of the month's records reached no rep. Unassigned records arrive email-only "
                                   f"and almost never convert; routing them is the largest lever on the pipeline that "
                                   f"costs no media money."))
        if un_share > 25:
            flags.append({"severity": "red", "title": "A large share of records reach no rep",
                          "body": reg.c(f"{P}.unassigned_story")})

    # -- fourteen-month routing rollup ------------------------------------------------
    if inp.routing_rollup is not None:
        rr = inp.routing_rollup
        r_months = sorted(m for m in inp.routing if m <= rm)[-14:]
        rl = _range_label(r_months[0], r_months[-1]) if r_months else "fourteen months"
        r.cnt("r14.unassigned_records", rr["records"], rl, source=SR, higher_is_better=False)
        r.cnt("r14.unassigned_conversions", rr["conversions"], rl, source=SR)
        r.pct("r14.unassigned_rate", rr["rate_pct"], rl, source=SR)
        a_sum = sum(int(v) for m in r_months for n, v in
                    ((n, inp.routing[m]["reps"][n]["assigned"]) for n in inp.routing[m]["reps"] if n != "unassigned"))
        c_sum = sum(int(v) for m in r_months for n, v in
                    ((n, inp.routing[m]["reps"][n]["converted"]) for n in inp.routing[m]["reps"] if n != "unassigned"))
        if a_sum:
            r.cnt("r14.assigned_converted", c_sum, rl, source=SR, note=f"of {a_sum} records routed to a rep")
            r.pct("r14.assigned_rate", Decimal(c_sum) / Decimal(a_sum) * 100, rl, source="computed:lead_routing")

    # -- twelve-month lead quality --------------------------------------------------
    window = core.window
    if all(m in inp.lead_quality for m in window):
        recs = sum(int(inp.lead_quality[m]["total_records"]) for m in window)
        phones = sum(int(inp.lead_quality[m]["with_phone"]) for m in window)
        conv = sum(int(inp.lead_quality[m]["customers"]) for m in window)
        rl = core.labels["r12"]
        r.cnt("r12.lead_records", recs, rl, source=SQ)
        r.cnt("r12.leads_converted", conv, rl, source=SQ)
        r.pct("r12.lead_conversion", Decimal(conv) / Decimal(recs) * 100, rl, source="computed:lead_quality")
        r.pct("r12.phone_capture", Decimal(phones) / Decimal(recs) * 100, rl, source="computed:lead_quality")
        labels = [_short(m) for m in window]
        core.charts["phone_capture_12m"] = chart_spec("line", labels,
                                                      [_d(inp.lead_quality[m]["phone_capture_pct"]) for m in window],
                                                      y_format="pct")
        core.charts["lead_records_12m"] = chart_spec("bar", labels,
                                                     [Decimal(int(inp.lead_quality[m]["total_records"])) for m in window],
                                                     emphasis_index=len(window) - 1, y_format="count")
        last3 = window[-3:]
        l3 = _range_label(last3[0], last3[-1])
        recs3 = sum(int(inp.lead_quality[m]["total_records"]) for m in last3)
        conv3 = sum(int(inp.lead_quality[m]["customers"]) for m in last3)
        r.cnt("r3.lead_records", recs3, l3, source=SQ)
        r.cnt("r3.leads_converted", conv3, l3, source=SQ)
        r.pct("r3.lead_conversion", Decimal(conv3) / Decimal(recs3) * 100, l3, source="computed:lead_quality")
        if lq is not None:
            cur_pc = _d(lq["phone_capture_pct"])
            avg_pc = Decimal(phones) / Decimal(recs) * 100
            r.claim(f"{P}.phone_capture_story", lambda: delta(cur_pc, avg_pc),
                    render=lambda ch: (f"Blended phone capture this month is {'above' if ch > 0 else 'below'} its "
                                       f"twelve-month average. Records routed to a rep almost always carry a phone "
                                       f"number; the unassigned ones almost never do."))
            flags.append({"severity": "green" if cur_pc >= avg_pc else "amber",
                          "title": "Phone capture against its twelve-month average",
                          "body": reg.c(f"{P}.phone_capture_story")})
    else:
        pending["context"] = f"Lead-quality snapshots do not cover the twelve months {window[0]}..{window[-1]}."

    # -- geography -----------------------------------------------------------------
    if inp.geography is None:
        pending["geography"] = "No geography snapshot has been ingested for this month."
    else:
        g = inp.geography
        w0, w1 = str(g["window"]).split("..")
        gl = _range_label(w0, w1)
        c0, c1 = str(g["m13_closed_cohorts"]).split("..")
        cl = f"{_range_label(c0, c1)} cohorts (first 90 days closed)"
        SG = "netsuite:geography_12mo"
        top = sorted(g["by_state"], key=lambda s: -int(s["customers"]))[:10]
        rows = []
        for s in top:
            st = str(s["state"])
            r.txt(f"geo.{st}.state", st, gl, source=SG)
            r.cnt(f"geo.{st}.customers", s["customers"], gl, source=SG)
            r.cur(f"geo.{st}.m1_net", s["m1_net_revenue"], gl, source=SG)
            r.cur(f"geo.{st}.first90_net", s["m13_net_revenue_closed_cohorts_only"], cl, source=SG)
            rows.append({"state": f"geo.{st}.state", "customers": f"geo.{st}.customers", "m1": f"geo.{st}.m1_net",
                         "first90": f"geo.{st}.first90_net", "status": None})
        core.tables["geography"] = {
            "columns": [
                {"key": "state", "label": "Ship-to state", "kind": "metric"},
                {"key": "customers", "label": "Customers", "kind": "metric", "align": "right"},
                {"key": "m1", "label": "Month-one NET", "kind": "metric", "align": "right"},
                {"key": "first90", "label": "First-90-days NET, closed cohorts", "kind": "metric", "align": "right"},
            ],
            "rows": rows,
        }
        r.cnt("geo.total_customers", g["total_customers"], gl, source=SG,
              note="customers created in the window who have bought at any time since")
        r.cnt("geo.no_state_customers", g["customers_with_no_state"], gl, source=SG, higher_is_better=False)
        core.charts["geo_customers"] = chart_spec("hbar", [str(s["state"]) for s in top],
                                                  [Decimal(int(s["customers"])) for s in top], y_format="count")

    core.flags = flags + core.flags
    context = core.base_context(
        {"title": "Marketing pipeline for sales", "slug": "sales",
         "subtitle": "What marketing put into the pipeline, who received it, how good it was, and where new business "
                     "is being created. Reps are named; this page is behind Cloudflare Access."},
        ["NetSuite (SuiteQL via MCP)"], inp)
    return context, problems


PAGES: dict[str, Callable[..., tuple[dict, list[str]]]] = {
    "executive": populate_executive,
    "marketing-ops": populate_marketing_ops,
    "sales": populate_sales,
}
