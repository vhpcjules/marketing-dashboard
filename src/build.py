"""The build: a pure function of the committed repository.

    python -m src.build --as-of 2026-09-05 [--dist dist] [--skip-gate]

No network. Everything the build reads is in git - snapshots under
data/snapshots/, manual files under data/manual/, the approved budget - so
the same commit produces the same dist/ on a laptop, in CI, and on
Cloudflare Pages. Cloudflare cannot reach NetSuite, and that is why ingest
(src/ingest/) is a separate phase that runs first, inside a Claude Code
session, and commits what it pulled.

What one build does, in order:

  1. Load: spend (SpendData), cohorts (frozen M1 + live repeat), the target,
     the budget, manual inputs (GMB, Hotjar - missing ones become "pending").
  2. Drift: compare every frozen figure against the latest live value the
     ingest phase recorded for it. Findings go to reports/restatement_<as_of>.md
     and to the console. The build FAILS on a breach it has not seen before;
     a breach already acknowledged in the frozen file (see `detect_frozen_drift`)
     is reported, not fatal, because the decision to hold the published
     figure was already made by a person.
  3. Registry: put every figure a page will show into the MetricRegistry as
     a src.units value. Nothing numeric reaches a template any other way.
  4. Render: each dashboard whose contract the registry satisfies, into
     dist/<slug>/index.html, with assets copied alongside. A page whose
     contract is NOT satisfied is skipped with the list of missing IDs and
     the build is marked not-ok. It is never rendered with gaps.
  5. Gate: src.validate.gate over dist/. Any failure -> exit 1.
  6. Change log: reports/change_log_<period>.md - for every series we track,
     the prior month, the new month, the relative change via src.units.delta,
     its direction, and whether the move exceeds the metric's variance
     threshold (default two standard deviations of the trailing twelve
     month-over-month changes, where twelve months exist).

Sibling modules (src.render, src.validate) are imported lazily and their
absence is logged, not papered over. The build never invents a figure, a
registry, or a validator to stand in for one that is missing: it says what
is missing and what that cost.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .data.cohorts import Cohort, CohortSet
from .data.spend import Basis, SpendData
from .data.targets import Pace, load_target
from .freeze import (DEFAULT_DRIFT_THRESHOLD_PCT, REPORTS, DriftFinding, DriftReport,
                     SnapshotStore, detect_drift)
from .ingest.common import MissingManualInput, month_label
from .ingest.manual import REQUIRED_FIELDS as MANUAL_DOMAINS, load_manual
from .ingest.netsuite import live_domain
from .periods import m13_closed, month_end, months_between, reporting_month, rolling_window, shift_month
from .units import Count, Money, Pct, Ratio, UndefinedDeltaError, arrow, delta, direction_class

__all__ = ["build", "BuildResult", "Inputs", "load_inputs", "detect_frozen_drift",
           "write_change_log", "change_log_rows", "variance_threshold", "main"]

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "assets"
PUBLIC = REPO_ROOT / "public"     # copied into dist/ verbatim: _redirects (the six v1 bookmarks), _headers
RUN_RATE_MONTHS = 4          # forecast run rate = mean M1 of the last four months (May-Aug for an August report)
HISTORY_MONTHS = 12          # variance threshold looks back this far
MIN_HISTORY = 6              # fewer month-over-month changes than this -> no threshold, say so
SD_MULTIPLIER = Decimal(2)

Log = Callable[[str], None]


def _d(x: Any) -> Decimal:
    """Decimal from a snapshot value. Snapshot JSON carries numbers; str() of
    the parsed float is the digits that were written (same rule as
    src.freeze.Snapshot.metric), so nothing is lost."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Sibling modules, imported lazily
# ---------------------------------------------------------------------------

class RegistryLike(Protocol):
    """The slice of src.render.registry.MetricRegistry the build relies on.

    A Protocol, not a stub: if the render layer is absent the build logs it
    and skips rendering. It never constructs its own registry.
    """

    def register(self, metric_id: str, value: Any, *, kind: str, period: str | None = None,
                 source: str, higher_is_better: bool = True, note: str | None = None,
                 fmt: str | None = None) -> Any: ...
    def register_claim(self, claim_id: str, expr: Callable[[], Any], assert_fn=None, render=None) -> None: ...
    def ids(self) -> list[str]: ...
    def claim_ids(self) -> list[str]: ...
    def unused(self) -> list[str]: ...
    def get(self, metric_id: str) -> Any: ...
    def c(self, claim_id: str) -> Any: ...


@dataclass
class Siblings:
    registry_cls: type | None = None
    render: Callable[..., str] | None = None
    chart_spec: Callable[..., dict] | None = None
    contracts: dict[str, Any] = field(default_factory=dict)      # slug -> PageContract
    run_gate: Callable[..., Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def can_render(self) -> bool:
        return all((self.registry_cls, self.render, self.chart_spec, self.contracts))


def _import_siblings(log: Log) -> Siblings:
    s = Siblings()
    try:
        from .render.registry import MetricRegistry
        from .render.env import render
        from .render.charts import chart_spec
        from .render import contracts as _contracts
        s.registry_cls, s.render, s.chart_spec = MetricRegistry, render, chart_spec
        for name in ("EXECUTIVE", "MARKETING_OPS", "SALES"):
            contract = getattr(_contracts, name, None)
            if contract is not None:
                s.contracts[contract.template.rsplit(".", 1)[0]] = contract
    except ImportError as e:
        s.notes.append(f"render layer not importable ({e}); no page will be rendered this build")
    try:
        from .validate.gate import run_gate
        s.run_gate = run_gate
    except ImportError as e:
        s.notes.append(f"validation gate not importable ({e}); dist/ will not be validated")
    for n in s.notes:
        log(f"build: {n}")
    return s


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class Inputs:
    as_of: date
    reporting_month: str
    store: SnapshotStore
    spend: SpendData
    prior_spend: SpendData | None
    cohorts: dict[str, Cohort]                    # every cohort month on disk
    repeat_source: dict[str, str]                 # month -> where live repeat came from
    m13: dict[str, dict]                          # closed first-90-days cohorts, body by month
    target: dict
    manual: dict[str, Any]                        # domain -> ManualInput | MissingManualInput
    lead_quality: dict[str, dict]                 # month -> body
    notes: list[str] = field(default_factory=list)

    @property
    def year(self) -> int:
        return int(self.reporting_month[:4])

    @property
    def prev_month(self) -> str:
        return shift_month(self.reporting_month, -1)


def _load_spend(year: int) -> SpendData | None:
    try:
        return SpendData.load(year)
    except FileNotFoundError:
        return None


def _load_cohorts(store: SnapshotStore) -> tuple[dict[str, Cohort], dict[str, str]]:
    """Frozen M1 + live repeat, per METHODOLOGY.md "The freeze decision".

    The live repeat component of a frozen cohort is read from the
    `cohorts_m1_live` sidecar the ingest phase writes (the frozen file itself
    cannot be rewritten). If no sidecar exists yet the frozen file's own
    repeat_revenue_live is used and labelled as stale.
    """
    cohorts, sources = {}, {}
    for month in store.periods("cohorts_m1"):
        snap = store.read(month, "cohorts_m1")
        m1 = snap.metric("m1_net_revenue")
        repeat = snap.metric("repeat_revenue_live")
        sources[month] = "cohorts_m1.json (open; pulled live)" if not snap.frozen else \
            f"repeat_revenue_live as of {snap.meta.get('pulled_at', '?')} (frozen file; no live sidecar yet)"
        if snap.frozen and store.exists(month, live_domain("cohorts_m1")):
            side = store.read(month, live_domain("cohorts_m1"))
            repeat = side.metric("repeat_revenue_live")
            sources[month] = f"cohorts_m1_live sidecar pulled {side.meta.get('pulled_at', '?')}"
        cohorts[month] = Cohort(month, int(snap.body["customers"]), m1, m1 + repeat)
    return cohorts, sources


def load_inputs(as_of: date, store: SnapshotStore | None = None, *, log: Log = print,
                manual_root: Path | None = None) -> Inputs:
    store = store or SnapshotStore()
    rm = reporting_month(as_of)
    year = int(rm[:4])
    spend = _load_spend(year)
    if spend is None:
        raise FileNotFoundError(f"no marketing_spend snapshots for {year}; run ingest first (src/ingest/README.md)")
    prior = _load_spend(year - 1)
    notes = []
    if prior is None:
        notes.append(f"no monthly marketing_spend snapshots for {year - 1}: prior-year spend and return "
                     f"per dollar cannot be shown until 'python -m src.ingest write marketing_spend "
                     f"{year - 1}-MM' has run for each month")
    cohorts, repeat_source = _load_cohorts(store)
    m13 = {m: store.read(m, "cohorts_m13").body for m in store.periods("cohorts_m13") if m13_closed(m, as_of)}
    manual = {}
    for domain in MANUAL_DOMAINS:
        manual[domain] = load_manual(domain, rm, manual_root) if manual_root else load_manual(domain, rm)
        if isinstance(manual[domain], MissingManualInput):
            notes.append(f"{domain}: {manual[domain].reason}")
    lead_quality = {m: store.read(m, "lead_quality").body for m in store.periods("lead_quality")}
    for n in notes:
        log(f"inputs: {n}")
    return Inputs(as_of, rm, store, spend, prior, cohorts, repeat_source, m13, load_target(year),
                  manual, lead_quality, notes)


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

def detect_frozen_drift(store: SnapshotStore, as_of: date, *,
                        threshold_pct: Decimal = DEFAULT_DRIFT_THRESHOLD_PCT
                        ) -> tuple[DriftReport, list[DriftFinding]]:
    """Frozen cohort figures against the latest live value; (report, NEW breaches).

    Two live values can exist for a frozen month. `live_at_last_pull` inside
    the frozen file is the value known WHEN THE FIGURE WAS FROZEN - the
    promotion note records that the difference was seen and the published
    figure was kept. A `cohorts_m1_live` sidecar, if the ingest phase has
    written one since, is the fresh value. Drift is measured against the
    freshest value available; a breach is NEW only if that value differs
    from the one already acknowledged in the frozen file. New breaches fail
    the build. Acknowledged ones are printed and written to the restatement
    report on every build, so nobody can say they were not told.
    """
    domain = "cohorts_m1"
    live: dict[str, dict[str, Any]] = {}
    acknowledged: dict[tuple[str, str], Decimal] = {}
    for month in store.frozen_periods(domain):
        snap = store.read(month, domain)
        known = snap.body.get("live_at_last_pull") or {}
        for metric, value in known.items():
            acknowledged[(month, metric)] = _d(value)
        fresh = None
        if store.exists(month, live_domain(domain)):
            fresh = store.read(month, live_domain(domain)).body.get("live_at_last_pull")
        values = fresh or known
        if values:
            live[month] = {k: v for k, v in values.items() if k in ("m1_net_revenue", "customers")}
    report = detect_drift(store, domain, live, ["m1_net_revenue", "customers"], as_of=as_of,
                          threshold_pct=threshold_pct)
    new = [f for f in report.breaches if acknowledged.get((f.period, f.metric)) != f.live_value]
    return report, new


# ---------------------------------------------------------------------------
# Registry population (executive page)
# ---------------------------------------------------------------------------

def _pid(month: str) -> str:
    """'2026-08' -> 'aug26' - the <period> half of a metric ID."""
    return f"{_MON[int(month[5:]) - 1].lower()}{month[2:4]}"


def _short(month: str) -> str:
    return f"{_MON[int(month[5:]) - 1]} {month[2:4]}"


def _range_label(start: str, end: str) -> str:
    a, b = month_label(start), month_label(end)
    return f"{a}–{b}" if start[:4] != end[:4] else f"{a.split()[0]}–{b}"


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _account_names(inp: Inputs) -> dict[str, str]:
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


def populate_executive(reg: RegistryLike, inp: Inputs, chart_spec: Callable[..., dict], *,
                       drift: DriftReport | None) -> tuple[dict, list[str]]:
    """Register every figure the executive page asks for; return (context, problems).

    `problems` lists what could not be registered and why. The caller checks
    the page contract afterwards; a non-empty list means the page is skipped.
    """
    problems: list[str] = []
    rm, pm, y = inp.reporting_month, inp.prev_month, inp.year
    P, Pp = _pid(rm), _pid(pm)
    ytd_months = months_between(f"{y}-01", rm)
    prior_ytd_months = months_between(f"{y - 1}-01", f"{y - 1}-{rm[5:]}")
    YTD, PYTD, FY = f"ytd{str(y)[2:]}", f"ytd{str(y - 1)[2:]}", f"fy{str(y)[2:]}"
    ytd_label, pytd_label = _range_label(ytd_months[0], rm), _range_label(prior_ytd_months[0], prior_ytd_months[-1])
    fy_label = f"FY{y}"
    NS = "netsuite:cohorts_m1"

    def cur(mid, amount, period, **kw):
        reg.register(mid, Money(amount, period), kind="currency", source=kw.pop("source", NS), **kw)

    def cnt(mid, n, period, **kw):
        reg.register(mid, Count(n, period), kind="count", source=kw.pop("source", NS), **kw)

    def pct(mid, v, period, **kw):
        reg.register(mid, Pct(v), kind="pct", period=period, source=kw.pop("source", "computed"), **kw)

    def rat(mid, v, period, fmt="per_dollar", **kw):
        reg.register(mid, Ratio(v), kind="ratio", period=period, fmt=fmt, source=kw.pop("source", "computed"), **kw)

    def txt(mid, s, period, **kw):
        reg.register(mid, s, kind="text", period=period, source=kw.pop("source", "computed"), **kw)

    true_monthly = inp.spend.monthly(Basis.TRUE_OPERATING)
    pending: dict[str, str] = {}

    # -- this month vs last ------------------------------------------------
    for month, pid in ((rm, P), (pm, Pp)):
        c = inp.cohorts.get(month)
        if c is None:
            problems.append(f"no cohorts_m1 snapshot for {month}")
            continue
        cnt(f"{pid}.new_customers", c.customers, month)
        cur(f"{pid}.m1_net", c.m1_net, month)
        cur(f"{pid}.avg_first_order", c.m1_net / Decimal(c.customers), month)
    if rm in inp.cohorts:
        spend_rm = true_monthly.get(rm)
        if spend_rm is None or spend_rm.amount <= 0:
            problems.append(f"no positive true-operating spend for {rm}; return per dollar undefined")
        else:
            rat(f"{P}.m1_return_per_dollar", inp.cohorts[rm].m1_net / spend_rm.amount, rm,
                source="computed:cohorts_m1/marketing_spend")

    # -- first-90-days, latest closed cohort (pendable) --------------------
    if inp.m13:
        latest = max(inp.m13)
        b = inp.m13[latest]
        label = f"{month_label(latest)} cohort"
        m1_live = _d(b["m1_net_revenue_live"])
        first90 = _d(b["m13_net_revenue"])
        txt("m13.latest.cohort", month_label(latest), label, source="netsuite:cohorts_m13")
        cnt("m13.latest.customers", int(b["customers_m13"]), label, source="netsuite:cohorts_m13")
        cur("m13.latest.m1_net", m1_live, label, source="netsuite:cohorts_m13",
            note="M1 from the same live pull as the 90-day figure, so the multiple is formed from one basis")
        cur("m13.latest.first90_net", first90, label, source="netsuite:cohorts_m13")
        if m1_live > 0:
            rat("m13.latest.multiple", first90 / m1_live, label, fmt="multiple")
        else:
            pending["m13_quality"] = f"The {month_label(latest)} cohort has no month-one revenue to form a multiple against."
    else:
        pending["m13_quality"] = ("No first-ninety-days cohort has both closed its window and been pulled; "
                                  "the section returns when the next cohorts_m13 snapshot lands.")

    # -- sources (pendable): no first-source snapshot domain exists yet ------
    pending["sources"] = ("First-source attribution has not been ingested: there is no sources snapshot "
                          "in data/snapshots/ for the twelve-month window.")

    # -- spending wisely -----------------------------------------------------
    ytd_cohorts = [inp.cohorts[m] for m in ytd_months if m in inp.cohorts]
    missing_ytd = [m for m in ytd_months if m not in inp.cohorts]
    if missing_ytd:
        problems.append(f"cohorts_m1 missing for {missing_ytd}; year-to-date figures cannot be formed")
    ytd_spend = inp.spend.window(ytd_months[0], rm, Basis.TRUE_OPERATING, label=ytd_label)
    cs = None
    if ytd_cohorts and not missing_ytd and ytd_spend.amount > 0:
        cs = CohortSet(ytd_label, ytd_cohorts, ytd_spend, inp.as_of)
        cur(f"{YTD}.spend", ytd_spend.amount, ytd_label, higher_is_better=False, source="netsuite:marketing_spend",
            note="true operating basis")
        rat(f"{YTD}.roas_m1", cs.roas_m1.value, ytd_label)
        rat(f"{YTD}.roas_to_date", cs.roas_to_date.value, ytd_label)
        mat = cs.avg_maturity_months.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        txt(f"{YTD}.roas_maturity", f"{mat} months average customer-weighted maturity", ytd_label)
        pct(f"{YTD}.repeat_share", cs.repeat_share, ytd_label)
        pct(f"{YTD}.spend_share_of_revenue", ytd_spend.amount / cs.m1_net.amount * Decimal(100), ytd_label,
            higher_is_better=False)
        cur(f"{YTD}.m1_net", cs.m1_net.amount, ytd_label)
        cnt(f"{YTD}.new_customers", cs.customers, ytd_label)
        rat(f"{YTD}.return_per_dollar", cs.roas_m1.value, ytd_label)
    elif ytd_spend.amount <= 0:
        problems.append(f"true-operating spend {ytd_label} is not positive")

    # -- pace against the target -------------------------------------------
    pace = None
    if cs is not None:
        target_amount = _d(inp.target["target_amount"])
        elapsed = int(rm[5:])
        remaining = 12 - elapsed
        rem_months = [f"{y - 1}-{m:02d}" for m in range(elapsed + 1, 13)]
        run_months = months_between(shift_month(rm, -(RUN_RATE_MONTHS - 1)), rm)
        if any(m not in inp.cohorts for m in rem_months):
            problems.append(f"prior-year cohorts {rem_months} incomplete; pace against target undefined")
        elif any(m not in inp.cohorts for m in run_months):
            problems.append(f"run-rate months {run_months} incomplete")
        else:
            prior_rem = sum((inp.cohorts[m].m1_net for m in rem_months), Decimal(0))
            run_rate = _mean([inp.cohorts[m].m1_net for m in run_months])
            pace = Pace(Money(target_amount, fy_label), Money(cs.m1_net.amount, fy_label), elapsed, remaining,
                        Money(prior_rem, fy_label), Money(run_rate, fy_label))
            cur(f"{FY}.target", target_amount, fy_label, source="manual:approved_marketing_budget.targets")
            if remaining:
                cur(f"{FY}.required_monthly", pace.required_monthly.amount, fy_label)
            else:
                cur(f"{FY}.required_monthly", pace.still_needed.amount, fy_label,
                    note="year complete: this is the full-year shortfall, not a monthly figure")
            cur(f"{FY}.forecast_at_run_rate", pace.forecast_at_run_rate.amount, fy_label,
                note=f"run rate = mean M1 of {_short(run_months[0])}–{_short(run_months[-1])}")

    # -- year over year ------------------------------------------------------
    prior_cohorts = [inp.cohorts[m] for m in prior_ytd_months if m in inp.cohorts]
    if len(prior_cohorts) == len(prior_ytd_months):
        p_m1 = sum((c.m1_net for c in prior_cohorts), Decimal(0))
        cur(f"{PYTD}.m1_net", p_m1, pytd_label)
        cnt(f"{PYTD}.new_customers", sum(c.customers for c in prior_cohorts), pytd_label)
        if inp.prior_spend is not None:
            try:
                p_spend = inp.prior_spend.window(prior_ytd_months[0], prior_ytd_months[-1],
                                                 Basis.TRUE_OPERATING, label=pytd_label)
            except ValueError:
                p_spend = None
            if p_spend is None or p_spend.amount <= 0:
                problems.append(f"prior-year spend for {pytd_label} is absent or not positive")
            else:
                cur(f"{PYTD}.spend", p_spend.amount, pytd_label, higher_is_better=False,
                    source="netsuite:marketing_spend")
                rat(f"{PYTD}.return_per_dollar", p_m1 / p_spend.amount, pytd_label)
        else:
            problems.append(f"no marketing_spend snapshots for {y - 1}: {PYTD}.spend and "
                            f"{PYTD}.return_per_dollar cannot be registered")
    else:
        problems.append(f"prior-year cohorts for {pytd_label} incomplete")

    # -- budget vs actual table ------------------------------------------------
    # Row labels go through the registry too. An account with no budget line
    # has no display name in the budget file, so its label is the GL code -
    # digits that must be traceable like any other figure on the page.
    names = _account_names(inp)
    rows = []
    for r in inp.spend.budget_vs_actual(ytd_months[0], rm):
        key = r["account"].replace(".", "_")
        if r["display"] != r["account"]:
            label = r["display"]
        elif r["account"] in names:
            label = f"{names[r['account']]} (GL {r['account']}, no budget line)"
        else:
            label = f"GL {r['account']} (no budget line)"
        txt(f"{YTD}.line.{key}", label, ytd_label, source="manual:approved_marketing_budget|netsuite:marketing_spend")
        cur(f"{YTD}.budget.{key}", r["budget"].amount, ytd_label, source="manual:approved_marketing_budget")
        cur(f"{YTD}.actual.{key}", r["actual"].amount, ytd_label, higher_is_better=False,
            source="netsuite:marketing_spend", note="as posted")
        cur(f"{YTD}.variance.{key}", r["variance"].amount, ytd_label, higher_is_better=False)
        status = "warn" if r["unbudgeted"] else ("danger" if r["variance"].amount > 0 else None)
        rows.append({"line": f"{YTD}.line.{key}", "budget": f"{YTD}.budget.{key}", "actual": f"{YTD}.actual.{key}",
                     "variance": f"{YTD}.variance.{key}", "status": status})
    budget_table = {
        "columns": [
            {"key": "line", "label": "Budget line", "kind": "metric"},
            {"key": "budget", "label": "Approved budget", "kind": "metric", "align": "right", "total": True},
            {"key": "actual", "label": "Actual (as posted)", "kind": "metric", "align": "right", "total": True},
            {"key": "variance", "label": "Variance", "kind": "metric", "align": "right", "total": True},
        ],
        "rows": rows,
    }

    # -- online (pendable) -----------------------------------------------------
    absent = [d for d in ("linkedin", "instagram", "meta_ads") if not inp.store.exists(rm, d)]
    if absent:
        pending["online"] = (f"Social and advertising snapshots for {month_label(rm)} have not been ingested "
                             f"({', '.join(absent)}); see src/ingest/README.md.")
    else:
        pending["online"] = ("Social snapshots are present but the online table is not yet assembled by the "
                             "build; the section returns when that wiring lands.")

    # -- charts ---------------------------------------------------------------
    window = rolling_window(inp.as_of, 12)
    charts: dict[str, dict] = {}
    if all(m in inp.cohorts for m in window):
        labels = [_short(m) for m in window]
        charts["new_customers_12m"] = chart_spec("bar", labels, [Decimal(inp.cohorts[m].customers) for m in window],
                                                 emphasis_index=len(window) - 1, y_format="count")
        charts["m1_net_12m"] = chart_spec("bar", labels, [inp.cohorts[m].m1_net for m in window],
                                          emphasis_index=len(window) - 1, y_format="usd")
    else:
        problems.append(f"cohorts_m1 missing for part of the twelve-month window {window[0]}..{window[-1]}")

    # -- claims: prose whose truth is checked when it renders ------------------
    if rm in inp.cohorts and pm in inp.cohorts:
        def _volume_change():
            return delta(inp.cohorts[rm].customers, inp.cohorts[pm].customers)
        reg.register_claim(f"{P}.volume_story", _volume_change,
                           render=lambda ch: ("New-customer volume rose against the month before." if ch > 0 else
                                              "New-customer volume fell against the month before." if ch < 0 else
                                              "New-customer volume matched the month before.")
                           + " Month-one revenue is a floor, not an estimate.")
    if cs is not None:
        reg.register_claim(f"{YTD}.roas_story", lambda: Pct(cs.repeat_share),
                           assert_fn=lambda p: Decimal(0) <= p.value < Decimal(100),
                           render=lambda p: f"Repeat revenue is {p} of what these cohorts have produced so far; "
                                            f"judging on month one alone leaves that out.")
    if pace is not None:
        behind = lambda: not pace.on_track  # noqa: E731
        reg.register_claim(f"{FY}.pace_story", behind,
                           render=lambda b: ("Behind at the current run rate: the remaining months must beat "
                                             "the same months last year." if b else
                                             "On track at the current run rate."))
        reg.register_claim(f"{FY}.on_track", behind,
                           render=lambda b: ("Not on track at the current run rate. The gap and what closing it "
                                             "would cost are priced on the Marketing Ops page." if b else
                                             "On track at the current run rate; hold the plan."))
    reg.register_claim("r12.sources_story",
                       lambda: pending.get("sources", ""),
                       render=lambda s: s or "First-source attribution is shown above.")

    # -- flags -----------------------------------------------------------------
    flags: list[dict] = []
    if pace is not None and not pace.on_track:
        flags.append({"severity": "red", "title": "Not on pace for the full-year target",
                      "body": reg.c(f"{FY}.on_track")})
    if drift is not None and drift.findings:
        reg.register_claim("build.drift_story", lambda: len({f.period for f in drift.findings}),
                           render=lambda n: f"{n} published cohort month(s) read differently in the latest live "
                                            f"pull. The published figures are held; the restatement report "
                                            f"lists every difference.")
        flags.append({"severity": "amber", "title": "Frozen figures have moved in the ledger",
                      "body": reg.c("build.drift_story")})
    missing_manual = [d.upper() for d, v in inp.manual.items() if isinstance(v, MissingManualInput)]
    if missing_manual:
        flags.append({"severity": "amber", "title": "Manual inputs pending",
                      "body": f"{' and '.join(missing_manual)} exports for the month have not been added under "
                              f"data/manual; their sections show as pending until they are."})

    context = {
        "page": {"title": "Executive dashboard", "slug": "executive",
                 "subtitle": "New customers, marketing return on both bases, and pace against the target. "
                             "All revenue is NET."},
        "months": [{"id": rm, "label": _short(rm)}],
        "active_month": rm,
        "prepared": {"iso": inp.as_of.isoformat(),
                     "label": f"{month_label(inp.as_of.isoformat()[:7]).split()[0]} {inp.as_of.day}, {inp.as_of.year}"},
        "data_sources": ["NetSuite (SuiteQL via MCP)", "Approved marketing budget (manual transcription)"],
        "asset_root": "/",
        "report": {
            "month_label": month_label(rm), "month_iso": rm, "prev_month_label": month_label(pm),
            "ytd_label": ytd_label, "ytd_iso": f"{ytd_months[0]}/{rm}",
            "prior_ytd_label": pytd_label, "prior_ytd_iso": f"{prior_ytd_months[0]}/{prior_ytd_months[-1]}",
            "r12_label": _range_label(window[0], window[-1]), "r12_iso": f"{window[0]}/{window[-1]}",
        },
        "charts": charts,
        "tables": {"budget_vs_actual": budget_table,
                   "online": {"columns": [], "rows": []}},
        "flags": flags,
        "pending": pending,
    }
    return context, problems


# ---------------------------------------------------------------------------
# Change log
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Series:
    key: str
    label: str
    kind: str                          # currency | count | pct
    values: Mapping[str, Decimal]      # month -> value
    higher_is_better: bool = True
    source: str = ""


def tracked_series(inp: Inputs) -> list[Series]:
    """Every monthly series the change log reports on."""
    out = [
        Series("new_customers", "New customers (M1 basis)", "count",
               {m: Decimal(c.customers) for m, c in inp.cohorts.items()}, source="cohorts_m1"),
        Series("m1_net_revenue", "Month-one NET revenue", "currency",
               {m: c.m1_net for m, c in inp.cohorts.items()}, source="cohorts_m1"),
        Series("avg_first_order", "Average first order", "currency",
               {m: c.m1_net / Decimal(c.customers) for m, c in inp.cohorts.items() if c.customers}, source="cohorts_m1"),
        Series("marketing_spend", "Marketing spend (true operating)", "currency",
               {m: v.amount for m, v in inp.spend.monthly(Basis.TRUE_OPERATING).items()},
               higher_is_better=False, source="marketing_spend"),
    ]
    if inp.prior_spend is not None:
        merged = dict(out[-1].values)
        merged.update({m: v.amount for m, v in inp.prior_spend.monthly(Basis.TRUE_OPERATING).items()})
        out[-1] = Series(out[-1].key, out[-1].label, "currency", merged, False, out[-1].source)
    lq = inp.lead_quality
    if lq:
        def col(key: str) -> dict[str, Decimal]:
            return {m: _d(b[key]) for m, b in lq.items() if b.get(key) is not None}
        out += [
            Series("lead_records", "Lead records created", "count", col("total_records"), source="lead_quality"),
            Series("phone_capture_pct", "Phone capture rate", "pct", col("phone_capture_pct"), source="lead_quality"),
            Series("email_capture_pct", "Email capture rate", "pct", col("email_capture_pct"), source="lead_quality"),
            Series("lead_conversion_pct", "Lead-to-customer conversion (to date)", "pct", col("conversion_pct"),
                   source="lead_quality"),
        ]
    return out


def _population_sd(xs: list[Decimal]) -> Decimal:
    mu = _mean(xs)
    var = sum(((x - mu) ** 2 for x in xs), Decimal(0)) / Decimal(len(xs))
    return var.sqrt()


def variance_threshold(series: Series, reporting: str, *, history_months: int = HISTORY_MONTHS,
                       min_history: int = MIN_HISTORY, multiplier: Decimal = SD_MULTIPLIER) -> Decimal | None:
    """Two standard deviations of the trailing month-over-month relative
    changes, ending the month BEFORE the reporting month. None when fewer
    than `min_history` changes exist - a threshold from three data points is
    noise wearing a number."""
    end = shift_month(reporting, -1)
    months = months_between(shift_month(end, -history_months), end)
    changes: list[Decimal] = []
    for a, b in zip(months, months[1:]):
        if a in series.values and b in series.values:
            try:
                changes.append(delta(series.values[b], series.values[a]))
            except UndefinedDeltaError:
                continue
    if len(changes) < min_history:
        return None
    return (_population_sd(changes) * multiplier).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _fmt(kind: str, value: Decimal, period: str) -> str:
    if kind == "currency":
        return Money(value, period).usd2
    if kind == "count":
        return Count(int(value), period).plain
    return Pct(value).pct1


_DIRECTION_WORD = {"delta-good": "better", "delta-bad": "worse", "delta-flat": "flat"}


def change_log_rows(series_list: list[Series], reporting: str,
                    thresholds: Mapping[str, Decimal] | None = None) -> list[dict[str, Any]]:
    prev = shift_month(reporting, -1)
    overrides = dict(thresholds or {})
    rows = []
    for s in series_list:
        new, old = s.values.get(reporting), s.values.get(prev)
        row: dict[str, Any] = {"metric": s.label, "key": s.key, "kind": s.kind, "source": s.source,
                               "prior": None if old is None else _fmt(s.kind, old, prev),
                               "new": None if new is None else _fmt(s.kind, new, reporting),
                               "change_pct": None, "direction": "n/a", "threshold_pct": None,
                               "threshold_source": None, "exceeds": None}
        if s.key in overrides:
            row["threshold_pct"], row["threshold_source"] = overrides[s.key], "configured"
        else:
            t = variance_threshold(s, reporting)
            row["threshold_pct"] = t
            row["threshold_source"] = None if t is None else f"{SD_MULTIPLIER} SD of trailing {HISTORY_MONTHS} months"
        if new is None or old is None:
            row["direction"] = "no prior month" if old is None else "no value this month"
            rows.append(row)
            continue
        try:
            change = delta(new, old)
        except UndefinedDeltaError:
            row["direction"] = "new (prior month was zero)"
            rows.append(row)
            continue
        change = change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        row["change_pct"] = change
        row["direction"] = f"{arrow(change)} {_DIRECTION_WORD[direction_class(change, s.higher_is_better)]}"
        if row["threshold_pct"] is not None:
            row["exceeds"] = abs(change) > row["threshold_pct"]
        rows.append(row)
    return rows


def write_change_log(rows: list[dict[str, Any]], reporting: str, as_of: date, reports_dir: Path = REPORTS,
                     *, notes: list[str] = ()) -> Path:
    prev = shift_month(reporting, -1)
    lines = [f"# Change log — {month_label(reporting)}", "",
             f"Built {as_of.isoformat()}. Each row compares {month_label(reporting)} with {month_label(prev)}. "
             f"Change is the relative percent change via `src.units.delta` — never a point difference, even for "
             f"rates. The threshold is {SD_MULTIPLIER} standard deviations of the trailing {HISTORY_MONTHS} "
             f"month-over-month changes where at least {MIN_HISTORY} exist, or a configured per-metric value; "
             f"a move beyond it is flagged for a human to read before publishing.", ""]
    if notes:
        lines += ["Build notes:", ""] + [f"- {n}" for n in notes] + [""]
    lines += [f"| Metric | Source | {_short(prev)} | {_short(reporting)} | Change | Direction | Threshold | Exceeds |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        chg = "—" if r["change_pct"] is None else f"{r['change_pct']:+}%"
        thr = "n/a (insufficient history)" if r["threshold_pct"] is None else \
            f"±{r['threshold_pct']}% ({r['threshold_source']})"
        exc = "—" if r["exceeds"] is None else ("**YES**" if r["exceeds"] else "no")
        lines.append(f"| {r['metric']} | {r['source']} | {r['prior'] or '—'} | {r['new'] or '—'} | {chg} | "
                     f"{r['direction']} | {thr} | {exc} |")
    flagged = [r["metric"] for r in rows if r["exceeds"]]
    lines += ["", f"Flagged: {', '.join(flagged) if flagged else 'none'}.", ""]
    reports_dir.mkdir(parents=True, exist_ok=True)
    p = reports_dir / f"change_log_{reporting}.md"
    p.write_text("\n".join(lines))
    return p


# ---------------------------------------------------------------------------
# dist/
# ---------------------------------------------------------------------------

def write_dist(dist: Path, pages: Mapping[str, str], *, assets: Path = ASSETS,
               public: Path = PUBLIC) -> list[Path]:
    """Pages under dist/<slug>/index.html, assets/ beside them, public/ verbatim.

    public/_redirects carries the six v1 bookmark redirects and the root
    rule; it is owned there (tests/test_repo_hygiene.py checks it) and the
    build never writes its own routing. If public/ has no _headers, one is
    added so no crawler indexes the site even if Access were misconfigured.
    """
    dist.mkdir(parents=True, exist_ok=True)
    written = []
    for slug, html in pages.items():
        out = dist / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        written.append(out)
    if assets.exists():
        target = dist / "assets"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(assets, target, ignore=shutil.ignore_patterns("*.md", "README*"))
    if public.exists():
        shutil.copytree(public, dist, dirs_exist_ok=True)
    if not (dist / "_headers").exists():
        (dist / "_headers").write_text("/*\n  X-Robots-Tag: noindex, nofollow\n")
    return written


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------

@dataclass
class BuildResult:
    as_of: date
    reporting_month: str
    dist: Path
    rendered: list[Path] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)      # slug -> reason
    pending: dict[str, str] = field(default_factory=dict)
    drift: DriftReport | None = None
    new_drift_breaches: list[DriftFinding] = field(default_factory=list)
    restatement_report: Path | None = None
    change_log: Path | None = None
    gate_ok: bool | None = None                                 # None = gate not run
    gate_report: Path | None = None
    unused_metrics: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.skipped and not self.new_drift_breaches and self.gate_ok is not False

    def summary(self) -> str:
        lines = [f"build {self.reporting_month} as of {self.as_of}: {'OK' if self.ok else 'NOT OK'}",
                 f"  rendered: {[str(p) for p in self.rendered] or 'nothing'}"]
        for slug, why in self.skipped.items():
            lines.append(f"  skipped {slug}: {why}")
        for k, v in self.pending.items():
            lines.append(f"  pending section {k}: {v}")
        if self.drift is not None:
            lines.append(f"  drift: {len(self.drift.findings)} finding(s), {len(self.drift.breaches)} breach(es), "
                         f"{len(self.new_drift_breaches)} NEW breach(es)")
        lines.append(f"  gate: {'not run' if self.gate_ok is None else ('passed' if self.gate_ok else 'FAILED')}")
        lines.append(f"  change log: {self.change_log}")
        if self.unused_metrics:
            lines.append(f"  registered but never displayed: {self.unused_metrics}")
        return "\n".join(lines)


def build(as_of: date, dist: Path = Path("dist"), *, skip_gate: bool = False,
          store: SnapshotStore | None = None, reports_dir: Path = REPORTS,
          variance_thresholds: Mapping[str, Decimal] | None = None,
          drift_threshold_pct: Decimal = DEFAULT_DRIFT_THRESHOLD_PCT,
          log: Log = print) -> BuildResult:
    """Run one build. Returns a BuildResult; `ok` is what the CLI's exit code follows."""
    dist = Path(dist)
    store = store or SnapshotStore()
    sib = _import_siblings(log)
    inp = load_inputs(as_of, store, log=log)
    result = BuildResult(as_of, inp.reporting_month, dist, notes=list(inp.notes) + list(sib.notes))
    log(f"build: reporting month {inp.reporting_month}; {len(inp.cohorts)} cohort months, "
        f"spend months {sorted(inp.spend.postings)}")

    # 2. drift
    report, new_breaches = detect_frozen_drift(store, as_of, threshold_pct=drift_threshold_pct)
    result.drift, result.new_drift_breaches = report, new_breaches
    log(report.console())
    if report.findings:
        result.restatement_report = report.write(reports_dir)
        log(f"build: restatement report written to {result.restatement_report}")
    if new_breaches:
        log(f"build: {len(new_breaches)} NEW drift breach(es) not yet acknowledged in the frozen file; "
            f"the build will not publish over them. Investigate, then hold (amend the frozen file's "
            f"live_at_last_pull with a reason) or accept (amend the figure).")

    # 3-4. registry + render
    pages: dict[str, str] = {}
    if sib.can_render:
        for slug, contract in sib.contracts.items():
            if slug != "executive":
                result.skipped[slug] = "the build has no registry population for this page yet"
                continue
            reg = sib.registry_cls()
            context, problems = populate_executive(reg, inp, sib.chart_spec, drift=report)
            result.pending.update(context["pending"])
            missing = contract.check(reg.ids(), reg.claim_ids(), context["pending"])
            if problems or missing:
                why = "; ".join(problems + ([f"contract IDs not registered: {missing}"] if missing else []))
                result.skipped[slug] = why
                log(f"build: SKIPPING {slug}: {why}")
                continue
            pages[slug] = sib.render(contract.template, context, registry=reg)
            result.unused_metrics = [i for i in reg.unused() if not i.startswith("build.")]
            log(f"build: rendered {slug} ({len(reg.ids())} metrics, {len(context['pending'])} pending section(s))")
    else:
        result.skipped["executive"] = "render layer absent (see notes)"
        log("build: render layer absent; skipping every page. dist/ gets assets and routing only.")
    result.rendered = write_dist(dist, pages)

    # 5. gate
    if skip_gate:
        log("build: gate skipped by request (--skip-gate). Do not deploy this dist.")
    elif sib.run_gate is None:
        log("build: gate unavailable; dist/ is UNVALIDATED")
    else:
        gate = sib.run_gate(dist, inp.reporting_month)
        result.gate_ok = gate.ok
        reports_dir.mkdir(parents=True, exist_ok=True)
        result.gate_report = reports_dir / f"gate_{inp.reporting_month}.md"
        result.gate_report.write_text(gate.to_markdown())
        log(gate.console())

    # 6. change log
    rows = change_log_rows(tracked_series(inp), inp.reporting_month, variance_thresholds)
    result.change_log = write_change_log(rows, inp.reporting_month, as_of, reports_dir, notes=result.notes)
    log(result.summary())
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.build", description="Build the dashboards from the committed repo.")
    ap.add_argument("--as-of", required=True, help="build date, YYYY-MM-DD; the reporting month is the one before it")
    ap.add_argument("--dist", default="dist", type=Path)
    ap.add_argument("--skip-gate", action="store_true", help="skip validation (never for a deploy)")
    ns = ap.parse_args(argv)
    result = build(date.fromisoformat(ns.as_of), ns.dist, skip_gate=ns.skip_gate)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
