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

import json

from .data.cohorts import Cohort
from .data.spend import Basis, SpendData
from .data.targets import load_target
from .freeze import (DEFAULT_DRIFT_THRESHOLD_PCT, REPORTS, DriftFinding, DriftReport,
                     SnapshotStore, detect_drift)
from .ingest.common import MissingManualInput, month_label
from .ingest.manual import REQUIRED_FIELDS as MANUAL_DOMAINS, load_manual
from .ingest.netsuite import live_domain
from .periods import m13_closed, months_between, reporting_month, shift_month
from .populate import PAGES, populate_executive  # noqa: F401  (populate_executive re-exported for callers)
from .units import Count, Money, Pct, UndefinedDeltaError, arrow, delta, direction_class

__all__ = ["build", "BuildResult", "Inputs", "load_inputs", "detect_frozen_drift",
           "write_change_log", "change_log_rows", "variance_threshold", "main", "populate_executive"]

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "assets"
PUBLIC = REPO_ROOT / "public"     # copied into dist/ verbatim: _redirects (the six v1 bookmarks), _headers
MANUAL_ROOT = REPO_ROOT / "data" / "manual"
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
                s.contracts[contract.slug] = contract
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
    routing: dict[str, dict] = field(default_factory=dict)          # month -> lead_routing body
    routing_rollup: dict | None = None                              # lead_routing_14mo_rollup for the reporting month
    source_mix: dict[str, dict] = field(default_factory=dict)       # month -> body
    source_mix_12mo: dict | None = None
    geography: dict | None = None
    retention: dict | None = None
    vintage: dict | None = None
    truad: dict | MissingManualInput | None = None                  # manual: agency platform media by month
    asks: dict | MissingManualInput | None = None                   # manual: priced budget asks
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
    routing = {m: store.read(m, "lead_routing").body for m in store.periods("lead_routing")}
    source_mix = {m: store.read(m, "source_mix").body for m in store.periods("source_mix")}

    def month_body(domain: str) -> dict | None:
        if store.exists(rm, domain):
            return store.read(rm, domain).body
        notes.append(f"no {domain} snapshot for {rm}; its section will show as pending")
        return None

    def manual_json(name: str) -> dict | MissingManualInput:
        p = (manual_root or MANUAL_ROOT) / str(year) / f"{name}.json"
        if not p.exists():
            notes.append(f"no {p.relative_to(REPO_ROOT) if p.is_relative_to(REPO_ROOT) else p}; "
                         f"sections that need it will show as pending")
            return MissingManualInput(name, rm, f"{p} does not exist")
        return json.loads(p.read_text())

    inputs = Inputs(as_of, rm, store, spend, prior, cohorts, repeat_source, m13, load_target(year),
                    manual, lead_quality, routing, month_body("lead_routing_14mo_rollup"), source_mix,
                    month_body("source_mix_12mo"), month_body("geography_12mo"), month_body("retention"),
                    month_body("acquisition_vintage"), manual_json("truad_media_spend"), manual_json("budget_asks"),
                    notes)
    for n in notes:
        log(f"inputs: {n}")
    return inputs


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


def _short(month: str) -> str:
    return f"{_MON[int(month[5:]) - 1]} {month[2:4]}"


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


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


def _narrative_for(slug: str, period: str, reg: Any, problems: list[str], log: Log) -> Any:
    """The month's story for one page: claims registered, prose resolved.

    A missing content file is a pending callout, not a failure (numbers are
    refreshed before the story is written). A malformed one, or one whose
    metric references the registry cannot satisfy, is a problem: the page
    must not ship with a half-resolved story.
    """
    from .render.narrative import NarrativeError, RenderedNarrative, load_narrative
    try:
        nar = load_narrative(period, slug)
    except NarrativeError as e:
        problems.append(f"narrative: {e}")
        return RenderedNarrative.pending(period, slug, str(e))
    if nar is None:
        reason = (f"No narrative has been written for {month_label(period)}: content/{period}/{slug}.md does not "
                  f"exist. The figures on this page are current; the story is not yet.")
        log(f"build: {slug}: {reason}")
        return RenderedNarrative.pending(period, slug, reason)
    try:
        nar.register_claims(reg)
        return nar.render(reg)
    except Exception as e:  # RegistryError, ClaimError, ClaimExprError, UndefinedError, NarrativeError
        problems.append(f"narrative {nar.path.name}: {type(e).__name__}: {e}")
        return RenderedNarrative.pending(period, slug, str(e))


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

    # 3-4. registry + render, one fresh registry per page
    pages: dict[str, str] = {}
    registered_all: set[str] = set()
    used_anywhere: set[str] = set()
    if sib.can_render:
        for slug, contract_tpl in sib.contracts.items():
            populate = PAGES.get(slug)
            if populate is None:
                result.skipped[slug] = "the build has no registry population for this page"
                continue
            contract = contract_tpl.for_period(inp.reporting_month)
            reg = sib.registry_cls()
            context, problems = populate(reg, inp, sib.chart_spec, drift=report)
            result.pending.update({f"{slug}:{k}": v for k, v in context["pending"].items()})
            narrative = _narrative_for(slug, inp.reporting_month, reg, problems, log)
            context["narrative"] = narrative
            missing = contract.check(reg.ids(), reg.claim_ids(), context["pending"])
            if problems or missing:
                why = "; ".join(problems + ([f"contract IDs not registered: {missing}"] if missing else []))
                result.skipped[slug] = why
                log(f"build: SKIPPING {slug}: {why}")
                continue
            html = sib.render(contract.template, context, registry=reg)
            unplaced = narrative.unplaced()
            if unplaced:
                why = (f"narrative sections written for this month but placed by no template slot: {unplaced}. "
                       f"Prose is shown or deliberately removed, never lost.")
                result.skipped[slug] = why
                log(f"build: SKIPPING {slug}: {why}")
                continue
            pages[slug] = html
            registered_all |= set(reg.ids())
            used_anywhere |= set(reg.accessed())
            log(f"build: rendered {slug} ({len(reg.ids())} metrics, {len(context['pending'])} pending section(s)"
                f"{'' if not narrative.is_pending else '; narrative pending'})")
        result.unused_metrics = sorted(i for i in registered_all - used_anywhere if not i.startswith("build."))
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
