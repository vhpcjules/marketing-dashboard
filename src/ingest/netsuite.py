"""NetSuite adapter: SuiteQL rows in, snapshot bodies out.

THE TOOL. In a Claude Code session the executor is

    mcp__NetSuite__ns_runCustomSuiteQL

called with the SQL string this module produces. Its constraints shape
everything below, and each was learned by hitting it:

  180-second timeout    Transaction joins over a multi-month range time out.
                        Chunk by month: one cohort or one calendar month per
                        call. Customer-table-only queries (lead_quality,
                        lead_routing) are cheap and may span the whole range.
  5000-row cap          Results are silently truncated at 5000 rows. Every
                        query here aggregates in SQL so a call returns a
                        handful of rows; `run()` still refuses a result that
                        reaches the cap, because a truncated total looks
                        exactly like a small one.
  no CTEs               No WITH clauses. Sub-selects are fine.
  TO_DATE literals      Dates go in as TO_DATE('YYYY-MM-DD', 'YYYY-MM-DD').
                        There are no bind parameters; src.ingest.queries
                        substitutes them and refuses anything unsafe.
  BUILTIN.DF(x)         The display name of a list/record id (account name,
                        employee name). BUILTIN.DF inside GROUP BY is
                        rejected in some contexts; group by the id and look
                        the name up separately.

Python never calls the tool. Claude does, and hands the rows to the adapter
- either directly (`NetSuiteAdapter(executor)` where the executor closes
over the tool) or via a JSON file through `python -m src.ingest write`. In
tests the executor is a fake returning canned rows. The adapter is the same
code in all three cases, which is the point.

Numbers arrive as strings or Decimals and stay Decimal. A float in a row is
refused (src.ingest.common.dec).

Frozen months. `ingest_*` functions never overwrite a frozen snapshot - the
store refuses, and months_to_pull() does not ask for them. But two things
about a frozen month are still worth pulling: the LIVE repeat revenue of a
frozen cohort (it grows; METHODOLOGY.md "frozen M1 + live repeat") and the
live value of the frozen figure itself, for drift detection. Both go to a
sidecar domain `<domain>_live`, which is never promoted. The build reads
the sidecar for repeat revenue and compares its `live_at_last_pull` against
the frozen file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..data.spend import GL_EXCLUDE_PREFIXES, GL_INCLUDE_PREFIXES, SUBSIDIARY_ID, in_scope
from ..freeze import SnapshotStore, query_hash
from ..periods import m13_closed, month_end, months_between
from .common import IngestError, Pull, dec, jsonable
from .queries import SEARCH_PATH, load_query, month_bounds

__all__ = [
    "NETSUITE_TOOL", "SUITEQL_TIMEOUT_SECONDS", "SUITEQL_ROW_CAP", "SOURCE", "LIVE_SUFFIX",
    "CAT_GARAGEEXPERTS", "CAT_VENDOR", "REP_IDS", "NetSuiteError", "RowCapError",
    "Executor", "NetSuiteAdapter", "ingest_marketing_spend", "ingest_cohort_m1",
    "ingest_cohorts_m13", "ingest_lead_quality", "ingest_lead_routing", "live_domain", "lead_params",
]

NETSUITE_TOOL = "mcp__NetSuite__ns_runCustomSuiteQL"
SUITEQL_TIMEOUT_SECONDS = 180
SUITEQL_ROW_CAP = 5000
SOURCE = "NetSuite SuiteQL via MCP, subsidiary 2"
LIVE_SUFFIX = "_live"

# Customer categories excluded from every revenue and lead figure. The two
# source documents disagreed about which id was which; BUILTIN.DF confirmed
# 2 = Garage Experts, 14 = Vendor on 2026-09-05. Both are excluded either
# way, so revenue is unaffected by the labels - but keep them named.
CAT_GARAGEEXPERTS = 2
CAT_VENDOR = 14

# Sales rep employee ids -> snapshot keys. Anything else is "other" and its
# display name is listed, so a new hire shows up as a name, not a silent
# bucket.
REP_IDS: Mapping[str, str] = {"8766": "alexis", "5803": "dan", "16226": "parker"}

Executor = Callable[[str], list[dict]]


class NetSuiteError(IngestError):
    pass


class RowCapError(NetSuiteError):
    """The result reached the 5000-row cap, so it is probably truncated."""


def live_domain(domain: str) -> str:
    return f"{domain}{LIVE_SUFFIX}"


def _one_row(rows: list[dict], what: str) -> dict:
    if len(rows) != 1:
        raise NetSuiteError(f"{what}: expected exactly one aggregate row, got {len(rows)}")
    return rows[0]


def _int(value: Any, where: str) -> int:
    d = dec(value, where)
    if d != d.to_integral_value():
        raise NetSuiteError(f"{where}: {value!r} is not a whole number")
    return int(d)


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """A rate out of 100, or None when the denominator is zero. Never a delta."""
    if denominator == 0:
        return None
    return (numerator / denominator * Decimal(100)).quantize(Decimal("0.1"))


def lead_params(months: Iterable[str]) -> dict[str, Any]:
    """Parameters for the two customer-table lead queries over a month range."""
    ms = sorted(months)
    return {"subsidiary_id": SUBSIDIARY_ID, "cat_garageexperts": CAT_GARAGEEXPERTS,
            "cat_vendor": CAT_VENDOR, "date_from": month_bounds(ms[0])[0],
            "date_to": month_bounds(ms[-1])[1]}


REPEAT_NOTE = ("Revenue beyond month one, pulled live each build. This component is never "
               "frozen because it grows; revenue-to-date on the frozen basis = frozen M1 + live repeat.")


@dataclass(frozen=True)
class NetSuiteAdapter:
    executor: Executor
    search_path: tuple[Path, ...] = SEARCH_PATH

    # -- plumbing ---------------------------------------------------------

    def run(self, name: str, params: Mapping[str, Any], *, statement: int | None = None) -> tuple[list[dict], str]:
        """Execute one query file; (rows, query_hash). Refuses a capped result."""
        sql, h = load_query(name, params, statement=statement, search_path=self.search_path)
        rows = [dict(r) for r in self.executor(sql)]
        if len(rows) >= SUITEQL_ROW_CAP:
            raise RowCapError(
                f"{name}: {len(rows)} rows reaches the SuiteQL cap of {SUITEQL_ROW_CAP}; the result is "
                f"probably truncated. Narrow the range or aggregate further in SQL."
            )
        return rows, h

    # -- marketing spend --------------------------------------------------

    def pull_marketing_spend(self, month: str) -> Pull:
        """{postings: {acct: amount}} for one calendar month, in-scope GL only."""
        d_from, d_to = month_bounds(month)
        rows, h = self.run("marketing_spend_monthly",
                           {"subsidiary_id": SUBSIDIARY_ID, "date_from": d_from, "date_to": d_to})
        postings: dict[str, Decimal] = {}
        names: dict[str, str] = {}
        for r in rows:
            if r.get("month") not in (None, month):
                raise NetSuiteError(f"marketing_spend {month}: row for {r.get('month')!r} in a one-month pull")
            acct = str(r["account"]).strip()
            # The SQL already excludes 96212.*, but the scope rule is asserted
            # again here: a loosened LIKE pattern would otherwise fold the
            # GarageExperts franchisee fund into VHPC spend silently.
            if not in_scope(acct):
                raise NetSuiteError(
                    f"marketing_spend {month}: account {acct!r} is outside {GL_INCLUDE_PREFIXES} "
                    f"excluding {GL_EXCLUDE_PREFIXES}; the query text has drifted"
                )
            postings[acct] = postings.get(acct, Decimal(0)) + dec(r["amount"], f"{acct}.amount")
            if r.get("account_name"):
                # BUILTIN.DF(a.id): kept so an unbudgeted account can be
                # labelled on the page by name rather than by GL code alone.
                names[acct] = str(r["account_name"]).strip()
        body = {"postings": dict(sorted(postings.items())), "account_names": dict(sorted(names.items()))}
        return Pull(jsonable(body), len(rows), h)

    # -- cohorts ----------------------------------------------------------

    def pull_cohort_m1(self, month: str, *, as_of: date) -> Pull:
        """{customers, m1_net_revenue, repeat_revenue_live, live_at_last_pull}.

        Two one-row queries: M1 (the figure that gets frozen) and revenue to
        date through `as_of` inclusive (the live component). The pull's hash
        fingerprints both queries.
        """
        c_from, c_to = month_bounds(month)
        m1_rows, h1 = self.run("cohorts_m1", {"cohort_from": c_from, "cohort_to": c_to})
        rtd_rows, h2 = self.run("cohorts_revenue_to_date",
                                {"cohort_from": c_from, "cohort_to": c_to,
                                 "through": as_of + timedelta(days=1)})
        m1 = _one_row(m1_rows, f"cohorts_m1 {month}")
        rtd = _one_row(rtd_rows, f"cohorts_revenue_to_date {month}")
        customers = _int(m1["customers_m1"], "customers_m1")
        m1_net = dec(m1["m1_net_revenue"], "m1_net_revenue")
        to_date = dec(rtd["revenue_to_date"], "revenue_to_date")
        if to_date < m1_net:
            raise NetSuiteError(
                f"cohort {month}: revenue to date ({to_date}) is below M1 ({m1_net}), which is impossible "
                f"unless credits were applied outside the M1 window - investigate before writing"
            )
        body = {
            "customers": customers,
            "m1_net_revenue": m1_net,
            "repeat_revenue_live": to_date - m1_net,
            "live_at_last_pull": {"customers": customers, "m1_net_revenue": m1_net,
                                  "revenue_to_date": to_date},
            "repeat_revenue_note": REPEAT_NOTE,
        }
        return Pull(jsonable(body), len(m1_rows) + len(rtd_rows), query_hash(f"{h1}+{h2}"))

    def pull_cohorts_m13(self, month: str, *, as_of: date) -> Pull:
        """First-90-days NET revenue for one cohort. Closed windows only."""
        if not m13_closed(month, as_of):
            raise NetSuiteError(
                f"cohorts_m13 {month}: the 90-day window closes on "
                f"{(month_end(month) + timedelta(days=90)).isoformat()}, after {as_of}; "
                f"a partial window must not be written as if complete"
            )
        c_from, c_to = month_bounds(month)
        rows, h = self.run("cohorts_m13", {"cohort_from": c_from, "cohort_to": c_to})
        r = _one_row(rows, f"cohorts_m13 {month}")
        body = {
            "customers_m13": _int(r["customers_m13"], "customers_m13"),
            "m13_net_revenue": dec(r["m13_net_revenue"], "m13_net_revenue"),
            "transactions_m13": _int(r.get("transactions", 0), "transactions"),
            "window_closed_on": month_end(month) + timedelta(days=90),
        }
        return Pull(jsonable(body), len(rows), h)

    # -- leads ------------------------------------------------------------

    def pull_lead_quality(self, months: Iterable[str]) -> dict[str, Pull]:
        """One Pull per month from a single customer-table query over the range.

        Rates are derived here, never in SQL, and every rate's denominator is
        in the body beside it.
        """
        months = list(months)
        rows, h = self.run("lead_quality", lead_params(months))
        by_month = {str(r["month"]): r for r in rows}
        out: dict[str, Pull] = {}
        for m in months:
            r = by_month.get(m)
            if r is None:
                continue                      # a month with no records is not a zero-row lie; skip it
            total = _int(r["total_records"], "total_records")
            phone = _int(r["with_phone"], "with_phone")
            email = _int(r["with_email"], "with_email")
            customers = _int(r["customers"], "customers")
            assigned = _int(r["assigned_records"], "assigned_records")
            a_phone = _int(r["assigned_with_phone"], "assigned_with_phone")
            a_email = _int(r["assigned_with_email"], "assigned_with_email")
            body = {
                "total_records": total, "with_phone": phone, "with_email": email,
                "customers": customers, "assigned_records": assigned,
                "assigned_with_phone": a_phone, "assigned_with_email": a_email,
                "phone_capture_pct": _pct(Decimal(phone), Decimal(total)),
                "email_capture_pct": _pct(Decimal(email), Decimal(total)),
                "conversion_pct": _pct(Decimal(customers), Decimal(total)),
                "assigned_phone_capture_pct": _pct(Decimal(a_phone), Decimal(assigned)),
                "assigned_email_capture_pct": _pct(Decimal(a_email), Decimal(assigned)),
            }
            out[m] = Pull(jsonable(body), 1, h)
        return out

    def pull_lead_routing(self, months: Iterable[str], rep_names: Mapping[str, str] | None = None) -> dict[str, Pull]:
        """Assigned / converted per rep per month. `rep_names` maps unknown
        rep ids to display names (from the companion BUILTIN.DF lookup)."""
        months = list(months)
        rows, h = self.run("lead_routing", lead_params(months))
        names = dict(rep_names or {})
        out: dict[str, Pull] = {}
        for m in months:
            reps: dict[str, dict] = {k: {"assigned": 0, "converted": 0} for k in REP_IDS.values()}
            reps["other"] = {"assigned": 0, "converted": 0, "names": []}
            reps["unassigned"] = {"assigned": 0, "converted": 0}
            seen = False
            for r in rows:
                if str(r["month"]) != m:
                    continue
                seen = True
                rep_id = r.get("rep_id")
                if rep_id in (None, ""):
                    key = "unassigned"
                else:
                    key = REP_IDS.get(str(rep_id), "other")
                    if key == "other":
                        label = names.get(str(rep_id), f"employee {rep_id}")
                        if label not in reps["other"]["names"]:
                            reps["other"]["names"].append(label)
                reps[key]["assigned"] += _int(r["assigned"], "assigned")
                reps[key]["converted"] += _int(r["converted"], "converted")
            if not seen:
                continue
            body = {"reps": reps, "total_records": sum(v["assigned"] for v in reps.values())}
            out[m] = Pull(jsonable(body), sum(1 for r in rows if str(r["month"]) == m), h)
        return out


# ---------------------------------------------------------------------------
# Writing: the only code that hands bodies to the store
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _write(store: SnapshotStore, month: str, domain: str, pull: Pull, *, query_id: str,
           pulled_at: datetime | None) -> Path:
    return store.write_open(month, domain, pull.body, query_id=query_id, query_hash_=pull.query_hash,
                            row_count=pull.row_count, pulled_at=pulled_at or _now(), source=SOURCE)


def _is_frozen(store: SnapshotStore, month: str, domain: str) -> bool:
    return store.exists(month, domain) and store.read(month, domain).frozen


def ingest_marketing_spend(adapter: NetSuiteAdapter, store: SnapshotStore, month: str, *,
                           pulled_at: datetime | None = None) -> Path:
    """Write marketing_spend for an open/closed month; a frozen month goes to
    the `_live` sidecar so the build can check it for drift."""
    pull = adapter.pull_marketing_spend(month)
    domain = "marketing_spend"
    if _is_frozen(store, month, domain):
        total = sum((dec(v) for v in pull.body["postings"].values()), Decimal(0))
        body = jsonable({"postings": pull.body["postings"], "live_at_last_pull": {"total": total},
                         "note": f"live re-pull of a FROZEN month for drift detection; {domain}.json is authoritative"})
        return _write(store, month, live_domain(domain), Pull(body, pull.row_count, pull.query_hash),
                      query_id="marketing_spend_monthly", pulled_at=pulled_at)
    return _write(store, month, domain, pull, query_id="marketing_spend_monthly", pulled_at=pulled_at)


def ingest_cohort_m1(adapter: NetSuiteAdapter, store: SnapshotStore, month: str, *, as_of: date,
                     pulled_at: datetime | None = None) -> Path:
    """Write cohorts_m1, or for a frozen cohort only its live components.

    The frozen file keeps the published M1 and customer count. The sidecar
    carries repeat_revenue_live (which the build adds to the frozen M1) and
    live_at_last_pull (which drift detection compares against the frozen
    figure). Nothing about the frozen file changes.
    """
    pull = adapter.pull_cohort_m1(month, as_of=as_of)
    domain = "cohorts_m1"
    if _is_frozen(store, month, domain):
        body = {k: pull.body[k] for k in ("repeat_revenue_live", "live_at_last_pull", "repeat_revenue_note")}
        body["note"] = f"live components of a FROZEN cohort; {domain}.json holds the published M1"
        return _write(store, month, live_domain(domain), Pull(body, pull.row_count, pull.query_hash),
                      query_id="cohorts_m1+revenue_to_date", pulled_at=pulled_at)
    return _write(store, month, domain, pull, query_id="cohorts_m1+revenue_to_date", pulled_at=pulled_at)


def ingest_cohorts_m13(adapter: NetSuiteAdapter, store: SnapshotStore, month: str, *, as_of: date,
                       pulled_at: datetime | None = None) -> Path:
    pull = adapter.pull_cohorts_m13(month, as_of=as_of)
    domain = "cohorts_m13"
    if _is_frozen(store, month, domain):
        body = dict(pull.body)
        body["live_at_last_pull"] = {"m13_net_revenue": pull.body["m13_net_revenue"],
                                     "customers_m13": pull.body["customers_m13"]}
        return _write(store, month, live_domain(domain), Pull(body, pull.row_count, pull.query_hash),
                      query_id="cohorts_m13_first_90_days", pulled_at=pulled_at)
    return _write(store, month, domain, pull, query_id="cohorts_m13_first_90_days", pulled_at=pulled_at)


def _ingest_monthly(store: SnapshotStore, pulls: Mapping[str, Pull], domain: str, query_id: str,
                    pulled_at: datetime | None) -> list[Path]:
    written = []
    for month, pull in sorted(pulls.items()):
        if _is_frozen(store, month, domain):
            continue                          # frozen lead months are simply left alone
        written.append(_write(store, month, domain, pull, query_id=query_id, pulled_at=pulled_at))
    return written


def ingest_lead_quality(adapter: NetSuiteAdapter, store: SnapshotStore, months: Iterable[str], *,
                        pulled_at: datetime | None = None) -> list[Path]:
    return _ingest_monthly(store, adapter.pull_lead_quality(months), "lead_quality",
                           "lead_quality_monthly", pulled_at)


def ingest_lead_routing(adapter: NetSuiteAdapter, store: SnapshotStore, months: Iterable[str], *,
                        rep_names: Mapping[str, str] | None = None,
                        pulled_at: datetime | None = None) -> list[Path]:
    return _ingest_monthly(store, adapter.pull_lead_routing(months, rep_names), "lead_routing",
                           "lead_routing_by_rep_month", pulled_at)


def lead_months(start: str, end: str) -> list[str]:
    """Convenience: the inclusive month range the two lead queries span."""
    return months_between(start, end)
