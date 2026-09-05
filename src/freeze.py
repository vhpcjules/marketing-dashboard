"""The freeze mechanism: snapshots, promotion, and drift detection.

The rule: a number that has been presented to leadership does not silently
change.

NetSuite is live. Invoices post late, credits land weeks after a sale. On
2026-09-04 a re-pull of nineteen published cohort months found five had moved,
all downward, by up to 4.9%. That is not a NetSuite bug - it is why this
module exists.

Layout: one file per period per domain.

    data/snapshots/2026-06/cohorts_m1.json
    data/snapshots/2026-06/marketing_spend.json

Each carries a `_meta` block: pulled_at, query_id, row_count, and `frozen`.
Sorted keys and stable indentation, so a git diff shows only real change.

Lifecycle:

  OPEN     -> written by every build from a live pull.
  FROZEN   -> written once by `promote`, then read-only. A build that finds a
              frozen file reads it and does NOT write it.
  drift    -> every build re-pulls a sample of frozen periods and diffs. A
              move is written to reports/restatement_<date>.md, printed loud,
              and fails the build above the threshold. The snapshot is never
              touched. Accepting a restatement is a human act: `amend`, which
              rewrites the snapshot in its own commit with a reason.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .periods import calendar_closed, m13_closed

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = REPO_ROOT / "data" / "snapshots"
REPORTS = REPO_ROOT / "reports"

DEFAULT_DRIFT_THRESHOLD_PCT = Decimal("1.0")

# Domains whose numbers are only meaningful once the cohort's 90-day window
# has closed. Promoting them earlier freezes a partial window as if complete.
M13_DOMAINS = {"cohorts_m13"}

__all__ = [
    "SnapshotStore", "Snapshot", "DriftFinding", "DriftReport",
    "detect_drift", "query_hash", "FreezeError",
]


class FreezeError(RuntimeError):
    pass


def query_hash(sql_text: str) -> str:
    """Stable fingerprint of the query that produced a snapshot.

    Recorded in _meta so a later reader can tell whether a figure changed
    because the data moved or because the query did.
    """
    return hashlib.sha256(sql_text.encode("utf-8")).hexdigest()[:16]


def _dec(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class Snapshot:
    domain: str
    period: str
    meta: dict
    body: dict

    @property
    def frozen(self) -> bool:
        return bool(self.meta.get("frozen"))

    def metric(self, key: str) -> Decimal:
        """A top-level numeric field, e.g. 'm1_net_revenue'."""
        if key not in self.body:
            raise KeyError(f"{self.domain}/{self.period} has no metric {key!r}")
        return _dec(self.body[key])


class SnapshotStore:
    def __init__(self, root: Path = SNAPSHOT_ROOT):
        self.root = root

    def path(self, period: str, domain: str) -> Path:
        return self.root / period / f"{domain}.json"

    def exists(self, period: str, domain: str) -> bool:
        return self.path(period, domain).exists()

    def read(self, period: str, domain: str) -> Snapshot:
        p = self.path(period, domain)
        if not p.exists():
            raise FileNotFoundError(f"no snapshot for {domain} {period} at {p}")
        raw = json.loads(p.read_text())
        meta = raw.pop("_meta", {})
        return Snapshot(domain, period, meta, raw)

    def periods(self, domain: str) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob(f"*/{domain}.json"))

    def frozen_periods(self, domain: str) -> list[str]:
        return [m for m in self.periods(domain) if self.read(m, domain).frozen]

    # -- writing ----------------------------------------------------------

    def write_open(self, period: str, domain: str, body: dict, *, query_id: str,
                   query_hash_: str | None, row_count: int, pulled_at: datetime,
                   source: str) -> Path:
        """Write a live pull. Refuses to overwrite a frozen snapshot.

        This is the guard that makes the freeze rule a property of the store
        rather than a convention of the caller.
        """
        if self.exists(period, domain) and self.read(period, domain).frozen:
            raise FreezeError(
                f"{domain} {period} is FROZEN. A live pull may not overwrite it. "
                f"If the figure has genuinely changed, run drift detection and, if "
                f"the restatement is accepted, `amend` it deliberately."
            )
        meta = {
            "domain": domain, "period": period, "frozen": False,
            "pulled_at": pulled_at.isoformat(timespec="seconds"),
            "query_id": query_id, "query_hash": query_hash_,
            "row_count": row_count, "source": source,
        }
        return self._write(period, domain, meta, body)

    def promote(self, period: str, domain: str, *, as_of: date, promoted_by: str,
                note: str = "") -> Path:
        """Freeze a period. Explicit, never automatic.

        Refuses if the calendar month is not over, and for M1-3 domains if the
        90-day window has not closed.
        """
        snap = self.read(period, domain)
        if snap.frozen:
            raise FreezeError(f"{domain} {period} is already frozen")
        if not calendar_closed(period, as_of):
            raise FreezeError(f"cannot promote {period}: calendar month not closed as of {as_of}")
        if domain in M13_DOMAINS and not m13_closed(period, as_of):
            raise FreezeError(
                f"cannot promote {domain} {period}: the 90-day window has not closed as of "
                f"{as_of}; freezing now would freeze a partial window as complete"
            )
        meta = dict(snap.meta)
        meta.update({"frozen": True, "promoted_at": as_of.isoformat(),
                     "promoted_by": promoted_by, "promotion_note": note})
        return self._write(period, domain, meta, snap.body)

    def amend(self, period: str, domain: str, new_body: dict, *, as_of: date,
              amended_by: str, reason: str) -> Path:
        """Deliberately replace a frozen figure. Requires a reason.

        The old values are kept inside the file under `_meta.amendments` so
        the record survives even outside git history.
        """
        if not reason or len(reason.strip()) < 20:
            raise FreezeError("amend requires a substantive reason (>= 20 characters)")
        snap = self.read(period, domain)
        if not snap.frozen:
            raise FreezeError(f"{domain} {period} is not frozen; use write_open")
        meta = dict(snap.meta)
        history = list(meta.get("amendments", []))
        history.append({"at": as_of.isoformat(), "by": amended_by, "reason": reason,
                        "previous": snap.body})
        meta["amendments"] = history
        return self._write(period, domain, meta, new_body)

    def _write(self, period: str, domain: str, meta: dict, body: dict) -> Path:
        p = self.path(period, domain)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"_meta": meta, **body}
        p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        return p


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftFinding:
    domain: str
    period: str
    metric: str
    frozen_value: Decimal
    live_value: Decimal

    @property
    def delta(self) -> Decimal:
        return self.live_value - self.frozen_value

    @property
    def delta_pct(self) -> Decimal | None:
        if self.frozen_value == 0:
            return None
        return self.delta / self.frozen_value * Decimal(100)

    def exceeds(self, threshold_pct: Decimal) -> bool:
        p = self.delta_pct
        return p is None or abs(p) > threshold_pct


@dataclass
class DriftReport:
    as_of: date
    threshold_pct: Decimal
    findings: list[DriftFinding] = field(default_factory=list)

    @property
    def breaches(self) -> list[DriftFinding]:
        return [f for f in self.findings if f.exceeds(self.threshold_pct)]

    @property
    def ok(self) -> bool:
        return not self.breaches

    def console(self) -> str:
        if not self.findings:
            return "drift: no frozen figure has moved"
        lines = ["", "=" * 72,
                 f"DRIFT DETECTED in {len(self.findings)} frozen figure(s); "
                 f"{len(self.breaches)} exceed the {self.threshold_pct}% threshold", "=" * 72]
        for f in self.findings:
            pct = "n/a" if f.delta_pct is None else f"{f.delta_pct:+.2f}%"
            flag = "  <<< BREACH" if f.exceeds(self.threshold_pct) else ""
            lines.append(f"  {f.domain:<16}{f.period:<9}{f.metric:<18}"
                         f"frozen {f.frozen_value:>14,.2f}  live {f.live_value:>14,.2f}  "
                         f"{f.delta:>+12,.2f}  {pct:>8}{flag}")
        lines += ["", "No snapshot was changed. To accept a restatement, use `amend` with a reason.",
                  "=" * 72, ""]
        return "\n".join(lines)

    def markdown(self) -> str:
        out = [f"# Restatement report — {self.as_of.isoformat()}", "",
               f"Drift threshold: {self.threshold_pct}%. "
               f"{len(self.findings)} frozen figure(s) moved; {len(self.breaches)} breach the threshold.",
               "", "**No snapshot was changed.** Accepting a restatement is a deliberate `amend`.", "",
               "| Domain | Period | Metric | Frozen | Live | Change | % |", "|---|---|---|---|---|---|---|"]
        for f in self.findings:
            pct = "n/a" if f.delta_pct is None else f"{f.delta_pct:+.2f}%"
            mark = " **breach**" if f.exceeds(self.threshold_pct) else ""
            out.append(f"| {f.domain} | {f.period} | {f.metric} | {f.frozen_value:,.2f} | "
                       f"{f.live_value:,.2f} | {f.delta:+,.2f} | {pct}{mark} |")
        return "\n".join(out) + "\n"

    def write(self, reports_dir: Path = REPORTS) -> Path:
        reports_dir.mkdir(parents=True, exist_ok=True)
        p = reports_dir / f"restatement_{self.as_of.isoformat()}.md"
        p.write_text(self.markdown())
        return p


ROUNDING_TOLERANCE = Decimal("0.5")   # frozen values were published to the dollar; cents are not drift


def detect_drift(store: SnapshotStore, domain: str, live: dict[str, dict[str, Any]],
                 metrics: Iterable[str], *, as_of: date,
                 threshold_pct: Decimal = DEFAULT_DRIFT_THRESHOLD_PCT,
                 tolerance: Decimal = ROUNDING_TOLERANCE) -> DriftReport:
    """Compare live values against FROZEN snapshots only.

    `live` is {period: {metric: value}}. Open periods are skipped - they are
    expected to move. Frozen periods that moved become findings; the snapshot
    is never written here.
    """
    report = DriftReport(as_of=as_of, threshold_pct=threshold_pct)
    for period, values in sorted(live.items()):
        if not store.exists(period, domain):
            continue
        snap = store.read(period, domain)
        if not snap.frozen:
            continue
        for metric in metrics:
            if metric not in values or metric not in snap.body:
                continue
            frozen_v, live_v = snap.metric(metric), _dec(values[metric])
            # Published figures were rounded to the dollar; a live value that
            # differs only in cents is rounding, not movement. Recording it as
            # drift would make "18 months moved" of a series where 5 did.
            if abs(frozen_v - live_v) > tolerance:
                report.findings.append(DriftFinding(domain, period, metric, frozen_v, live_v))
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m src.freeze",
                                 description="Promote, amend, or inspect frozen snapshots.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("promote", help="freeze a closed period (explicit, never automatic)")
    p.add_argument("period"); p.add_argument("domain")
    p.add_argument("--by", required=True); p.add_argument("--note", default="")
    p.add_argument("--as-of", default=date.today().isoformat())

    a = sub.add_parser("amend", help="deliberately replace a frozen figure")
    a.add_argument("period"); a.add_argument("domain")
    a.add_argument("--by", required=True); a.add_argument("--reason", required=True)
    a.add_argument("--body-json", required=True, help="path to a JSON file with the new body")
    a.add_argument("--as-of", default=date.today().isoformat())

    s = sub.add_parser("status", help="list periods and their state for a domain")
    s.add_argument("domain")

    ns = ap.parse_args(argv)
    store = SnapshotStore()
    if ns.cmd == "promote":
        path = store.promote(ns.period, ns.domain, as_of=date.fromisoformat(ns.as_of),
                             promoted_by=ns.by, note=ns.note)
        print(f"promoted {ns.domain} {ns.period} -> {path}\n"
              f"commit this file on its own: the commit message is the audit trail.")
    elif ns.cmd == "amend":
        body = json.loads(Path(ns.body_json).read_text())
        path = store.amend(ns.period, ns.domain, body, as_of=date.fromisoformat(ns.as_of),
                           amended_by=ns.by, reason=ns.reason)
        print(f"amended {ns.domain} {ns.period} -> {path}\ncommit with the reason in the message.")
    elif ns.cmd == "status":
        for period in store.periods(ns.domain):
            snap = store.read(period, ns.domain)
            print(f"{period}  {'FROZEN' if snap.frozen else 'open  '}  "
                  f"pulled {snap.meta.get('pulled_at','?')}  promoted {snap.meta.get('promoted_at') or '-'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli(sys.argv[1:]))
