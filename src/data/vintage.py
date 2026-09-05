"""Account vintage: when was each active account acquired, and what is it worth?

The load-bearing finding of the August deck was that legacy accounts are
worth an order of magnitude more per year than newly acquired ones. It was
computed on Sage created dates that were never in the repo, and NetSuite
cannot reproduce it: every account migrated from Sage in Q4 2024 carries the
migration date as `datecreated`.

Jules supplied the Sage "Customer Sales History by Period" reports for
2019 through September 2024. They carry no created date either, but they do
carry annual sales per Sage customer, so an account's first year with sales
is known back to 2019. That gives a defensible acquisition year for every
account Sage saw, with one honest limit: an account already selling in 2019
is "2019 or earlier", never "2012". The pre-2018 band v1 published is not
recoverable from these files and is not claimed here.

The join happens offline:

    NetSuite (vintage_accounts.sql)  ->  entityid '0000004 Artistic Concrete', FY NET revenue
    Sage manual file                  ->  '0000004' -> first_sale_year 2019 (floor)

    acquisition_year = Sage first-sale year if the Sage number matches,
                       else the NetSuite creation year (a genuinely new account)

Bands are then formed and each band's accounts, NET revenue, shares and
revenue per account are computed with Decimal. Nothing here reads NetSuite;
the build consumes a `vintage_accounts` snapshot the ingest phase wrote.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = ["SAGE_FILE", "SageHistory", "load_sage", "acquisition_year", "band_for", "vintage_table",
           "LEGACY_BAND", "BANDS"]

REPO_ROOT = Path(__file__).resolve().parents[2]
SAGE_FILE = REPO_ROOT / "data" / "manual" / "sage" / "customer_sales_history_2019_2024.json"
SAGE_FLOOR_YEAR = 2019

LEGACY_BAND = "2019 or earlier"
# (label, lo_year, hi_year) inclusive; the floor band absorbs everything at or before 2019.
BANDS: tuple[tuple[str, int | None, int], ...] = (
    (LEGACY_BAND, None, 2019),
    ("2020", 2020, 2020),
    ("2021", 2021, 2021),
    ("2022", 2022, 2022),
    ("2023", 2023, 2023),
    ("2024", 2024, 2024),
    ("2025", 2025, 2025),
    ("2026", 2026, 2026),
)

_SAGE_ID = re.compile(r"^([A-Za-z0-9&]+)\b")


def _d(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class SageHistory:
    first_sale_year: Mapping[str, int]      # Sage customer number -> first calendar year with sales
    meta: Mapping[str, Any]

    def sage_id(self, entityid: str) -> str | None:
        """The Sage customer number if the NetSuite entityid starts with one we know."""
        m = _SAGE_ID.match(str(entityid or "").strip())
        if not m:
            return None
        key = m.group(1)
        return key if key in self.first_sale_year else None

    def __len__(self) -> int:
        return len(self.first_sale_year)


def load_sage(path: Path = SAGE_FILE) -> SageHistory:
    doc = json.loads(Path(path).read_text())
    first = {cid: int(c["first_sale_year"]) for cid, c in doc["customers"].items()
             if c.get("first_sale_year") is not None}
    return SageHistory(first, doc.get("_meta", {}))


def acquisition_year(entityid: str, datecreated_year: int | str | None, sage: SageHistory) -> tuple[int, str]:
    """(year, basis). basis is 'sage' or 'netsuite'.

    Sage wins when it knows the account: its first-sale year is at worst a
    floor (2019), while the NetSuite creation year of a migrated account is
    simply wrong. An account Sage never saw was created in NetSuite and its
    creation year is real.
    """
    sid = sage.sage_id(entityid)
    if sid is not None:
        return sage.first_sale_year[sid], "sage"
    if datecreated_year in (None, ""):
        raise ValueError(f"account {entityid!r}: no Sage match and no NetSuite creation year")
    return int(datecreated_year), "netsuite"


def band_for(year: int) -> str:
    for label, lo, hi in BANDS:
        if (lo is None or year >= lo) and year <= hi:
            return label
    return str(year)


def vintage_table(rows: Iterable[Mapping[str, Any]], sage: SageHistory) -> dict[str, Any]:
    """Band an account list (vintage_accounts.sql rows) by acquisition year.

    Returns {bands: [{band, accounts, net_revenue, share_of_accounts_pct,
    share_of_revenue_pct, revenue_per_account}], totals, matched_sage,
    matched_netsuite}. Accounts with net revenue <= 0 in the window are kept
    in the totals (they are company revenue) but the 'active' definition used
    for shares is NET-positive, matching acquisition_vintage.json.
    """
    per_band: dict[str, dict[str, Any]] = {label: {"band": label, "accounts": 0, "net_revenue": Decimal(0),
                                                    "sage_dated": 0} for label, _, _ in BANDS}
    total_accounts, total_rev = 0, Decimal(0)
    matched = {"sage": 0, "netsuite": 0}
    for r in rows:
        rev = _d(r["net_revenue"])
        year, basis = acquisition_year(r.get("entityid", ""), r.get("datecreated_year"), sage)
        matched[basis] += 1
        b = per_band.setdefault(band_for(year), {"band": band_for(year), "accounts": 0, "net_revenue": Decimal(0),
                                                 "sage_dated": 0})
        b["net_revenue"] += rev
        total_rev += rev
        if rev > 0:
            b["accounts"] += 1
            total_accounts += 1
            if basis == "sage":
                b["sage_dated"] += 1
    out = []
    for label, _, _ in BANDS:
        b = per_band[label]
        n, rev = b["accounts"], b["net_revenue"]
        out.append({
            "band": label, "accounts": n, "net_revenue": rev, "sage_dated_accounts": b["sage_dated"],
            "share_of_accounts_pct": (Decimal(n) / Decimal(total_accounts) * 100) if total_accounts else Decimal(0),
            "share_of_revenue_pct": (rev / total_rev * 100) if total_rev else Decimal(0),
            "revenue_per_account": (rev / Decimal(n)) if n else Decimal(0),
        })
    return {"bands": out, "total_accounts": total_accounts, "total_net_revenue": total_rev,
            "matched_sage": matched["sage"], "matched_netsuite": matched["netsuite"],
            "legacy_band": LEGACY_BAND, "sage_floor_year": SAGE_FLOOR_YEAR}
