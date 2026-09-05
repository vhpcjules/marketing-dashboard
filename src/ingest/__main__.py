"""The ingest command line, for use inside a Claude Code session.

Python cannot call an MCP tool. Claude can. So the loop is: this CLI prints
the exact SQL (or Supermetrics arguments) to run, Claude runs the tool and
saves the rows to a JSON file, and this CLI turns the file into a snapshot.

    python -m src.ingest plan   <domain> --as-of 2026-09-05 [--from 2025-09]
    python -m src.ingest sql    <netsuite domain> <month> [--as-of ...]
    python -m src.ingest spec   <supermetrics domain> <month>
    python -m src.ingest write  <domain> <month> --rows a.json [b.json] [--as-of ...] [--range START END]
    python -m src.ingest manual <gmb|hotjar> <month>

`write` feeds the saved rows through the SAME adapter code a live executor
would, so a snapshot written this way is indistinguishable from one written
by a programmatic executor. The row files are consumed in the order the
adapter issues its queries (cohorts_m1: M1 then revenue-to-date; linkedin:
page statistics then share statistics) - `sql`/`spec` print them in that
order.

Row file shapes accepted: a JSON list of row objects; {"rows": [...]};
{"data": [...]} where data is a list of objects or a list of lists whose
first row is the header (the Supermetrics results shape); {"items": [...]}
(the NetSuite tool's shape). A Supermetrics file may carry the result range
under meta.start_date / meta.end_date; otherwise pass --range.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from ..freeze import SnapshotStore
from ..periods import months_between, reporting_month
from . import manual as manual_mod
from . import netsuite as ns
from . import supermetrics as sm
from .queries import load_query, month_bounds, months_to_pull

NETSUITE_DOMAINS = ("marketing_spend", "cohorts_m1", "cohorts_m13", "lead_quality", "lead_routing")
SUPERMETRICS_DOMAINS = tuple(sm.SOURCES)
MANUAL_DOMAINS = tuple(manual_mod.REQUIRED_FIELDS)


def _rows_from_file(path: Path) -> tuple[list[dict], dict[str, Any]]:
    raw = json.loads(Path(path).read_text(), parse_float=str, parse_int=str)
    meta: dict[str, Any] = {}
    data: Any = raw
    if isinstance(raw, dict):
        meta = raw.get("meta") or raw.get("query") or {}
        for key in ("rows", "items", "data", "results"):
            if key in raw:
                data = raw[key]
                break
        else:
            raise SystemExit(f"{path}: no rows/items/data key in the object")
    if not isinstance(data, list):
        raise SystemExit(f"{path}: rows are not a list")
    if data and isinstance(data[0], list):
        header, body = data[0], data[1:]
        data = [dict(zip(header, r)) for r in body]
    return [dict(r) for r in data], meta


class _FileExecutor:
    """Hands back the saved row files in order; refuses to run short."""

    def __init__(self, files: list[Path], range_: tuple[date, date] | None):
        self.files = list(files)
        self.range = range_
        self.calls = 0

    def netsuite(self, sql: str) -> list[dict]:
        if not self.files:
            raise SystemExit("the adapter issued more queries than row files were given")
        rows, _ = _rows_from_file(self.files.pop(0))
        self.calls += 1
        return rows

    def supermetrics(self, spec: sm.QuerySpec) -> sm.QueryResult:
        if not self.files:
            raise SystemExit("the adapter issued more queries than row files were given")
        rows, meta = _rows_from_file(self.files.pop(0))
        self.calls += 1
        if self.range:
            start, end = self.range
        elif meta.get("start_date") and meta.get("end_date"):
            start, end = date.fromisoformat(str(meta["start_date"])[:10]), date.fromisoformat(str(meta["end_date"])[:10])
        else:
            raise SystemExit("the result file carries no meta.start_date/end_date; pass --range START END "
                             "with the range the query actually covered")
        return sm.QueryResult(tuple(rows), start, end)


def _print_sql(domain: str, month: str, as_of: date) -> None:
    a, b = month_bounds(month)
    if domain == "marketing_spend":
        qs = [("marketing_spend_monthly", {"subsidiary_id": ns.SUBSIDIARY_ID, "date_from": a, "date_to": b}, None)]
    elif domain == "cohorts_m1":
        from datetime import timedelta
        qs = [("cohorts_m1", {"cohort_from": a, "cohort_to": b}, None),
              ("cohorts_revenue_to_date", {"cohort_from": a, "cohort_to": b, "through": as_of + timedelta(days=1)}, None)]
    elif domain == "cohorts_m13":
        qs = [("cohorts_m13", {"cohort_from": a, "cohort_to": b}, None)]
    else:  # lead_quality / lead_routing take a range; month is the END of it
        start = f"{int(month[:4]) - 1}-{month[5:]}"
        qs = [(domain, ns.lead_params(months_between(start, month)), None)]
        print(f"-- range {start}..{month}; one call covers it (customer table only)", file=sys.stderr)
    for i, (name, params, stmt) in enumerate(qs, 1):
        sql, h = load_query(name, params, statement=stmt)
        print(f"-- [{i}/{len(qs)}] {name}  query_hash={h}  tool={ns.NETSUITE_TOOL}")
        print(sql)
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    today = date.today().isoformat()

    p = sub.add_parser("plan", help="months that still need a pull (never frozen ones)")
    p.add_argument("domain"); p.add_argument("--as-of", default=today)
    p.add_argument("--from", dest="from_", help="first month, YYYY-MM (default: 14 months back)")

    s = sub.add_parser("sql", help="print the SuiteQL to run for a NetSuite domain and month")
    s.add_argument("domain", choices=NETSUITE_DOMAINS); s.add_argument("month"); s.add_argument("--as-of", default=today)

    q = sub.add_parser("spec", help="print the data_query arguments for a Supermetrics domain and month")
    q.add_argument("domain", choices=SUPERMETRICS_DOMAINS); q.add_argument("month")

    w = sub.add_parser("write", help="turn saved tool results into an open snapshot")
    w.add_argument("domain", choices=NETSUITE_DOMAINS + SUPERMETRICS_DOMAINS); w.add_argument("month")
    w.add_argument("--rows", nargs="+", required=True, type=Path, help="row files, in query order")
    w.add_argument("--as-of", default=today)
    w.add_argument("--range", nargs=2, metavar=("START", "END"), help="result date range (Supermetrics)")
    w.add_argument("--rep-names", type=Path, help="lead_routing: JSON {rep_id: display name} for unknown reps")

    m = sub.add_parser("manual", help="write a GMB/Hotjar file as a snapshot (missing file = pending)")
    m.add_argument("domain", choices=MANUAL_DOMAINS); m.add_argument("month")

    ns_ = ap.parse_args(argv)
    store = SnapshotStore()
    as_of = date.fromisoformat(getattr(ns_, "as_of", today))

    if ns_.cmd == "plan":
        end = reporting_month(as_of)
        start = ns_.from_ or f"{int(end[:4]) - 1}-{end[5:]}"
        for mo in months_to_pull(store, ns_.domain, months_between(start, end), as_of):
            print(mo)
        return 0

    if ns_.cmd == "sql":
        _print_sql(ns_.domain, ns_.month, as_of)
        return 0

    if ns_.cmd == "spec":
        for i, spec in enumerate(sm.specs_for(ns_.domain, ns_.month), 1):
            print(f"# [{i}] {spec.label}  fingerprint={spec.fingerprint()}  tool={sm.DATA_QUERY_TOOL}")
            print(json.dumps(spec.tool_args(), indent=2))
            print(f"# then poll {sm.RESULTS_TOOL} with the returned schedule_id and save the rows to a file")
        return 0

    if ns_.cmd == "write":
        rng = tuple(date.fromisoformat(x) for x in ns_.range) if ns_.range else None
        fx = _FileExecutor(ns_.rows, rng)
        if ns_.domain in NETSUITE_DOMAINS:
            adapter = ns.NetSuiteAdapter(fx.netsuite)
            if ns_.domain == "marketing_spend":
                paths = [ns.ingest_marketing_spend(adapter, store, ns_.month)]
            elif ns_.domain == "cohorts_m1":
                paths = [ns.ingest_cohort_m1(adapter, store, ns_.month, as_of=as_of)]
            elif ns_.domain == "cohorts_m13":
                paths = [ns.ingest_cohorts_m13(adapter, store, ns_.month, as_of=as_of)]
            else:
                start = f"{int(ns_.month[:4]) - 1}-{ns_.month[5:]}"
                months = months_between(start, ns_.month)
                if ns_.domain == "lead_quality":
                    paths = ns.ingest_lead_quality(adapter, store, months)
                else:
                    names = json.loads(ns_.rep_names.read_text()) if ns_.rep_names else None
                    paths = ns.ingest_lead_routing(adapter, store, months, rep_names=names)
        else:
            adapter = sm.SupermetricsAdapter(fx.supermetrics)
            paths = [sm.ingest_supermetrics(adapter, store, ns_.domain, ns_.month, as_of=as_of)]
        for pth in paths:
            print(f"wrote {pth}")
        if fx.files:
            print(f"warning: {len(fx.files)} row file(s) were not consumed", file=sys.stderr)
        return 0

    if ns_.cmd == "manual":
        out = manual_mod.ingest_manual(store, ns_.domain, ns_.month)
        if isinstance(out, manual_mod.MissingManualInput):
            print(f"pending: {out.reason}")
            return 0
        print(f"wrote {out}")
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
