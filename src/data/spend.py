"""Marketing spend: approved budget, GL actuals, and the three bases.

Marketing spend is GL accounts 66212.* and 66215.*, excluding 96212.* (the
NAF, which is the GarageExperts franchisee fund and not ours).

The subtlety that this module exists for is that "spend" is not one number.
Corrections posted in one month can belong to a different month, or to a
different YEAR, and blending them into a monthly series produces nonsense.
In August 2026 the raw GL nets to MINUS $9,493 - which, published unguarded,
would have made every August efficiency metric negative.

Three bases, and each has exactly one job:

  AS_POSTED       Raw GL, no adjustment. What the ledger says for a window.
                  This is what was published for Jan-Jul 2026 ($136,891) and
                  what the freeze rule holds frozen.

  TRUE_OPERATING  VHPC's actual marketing activity, by the month the activity
                  happened. Current-year misbookings are removed from the
                  months they were posted to; prior-year corrections are
                  excluded entirely. THIS IS THE BASIS FOR ALL EFFICIENCY
                  METRICS - return per dollar, cost per customer, spend as a
                  share of revenue.

  ANNUAL_LEDGER   As-posted for the full year, including prior-year
                  corrections. Used only for the annual budget-performance
                  review, because the credits do land on this year's books.

Budget lives in data/manual/, not NetSuite: report -197 returns Budget Amount
of zero for every account and at the subsidiary grand-total line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Iterable

from ..units import Money, Ratio, delta

REPO_ROOT = Path(__file__).resolve().parents[2]

GL_INCLUDE_PREFIXES = ("66212", "66215")
GL_EXCLUDE_PREFIXES = ("96212",)
SUBSIDIARY = "VHPC LLC"
SUBSIDIARY_ID = 2

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class Basis(str, Enum):
    AS_POSTED = "as_posted"
    TRUE_OPERATING = "true_operating"
    ANNUAL_LEDGER = "annual_ledger"


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def in_scope(account: str,
             include: Iterable[str] = GL_INCLUDE_PREFIXES,
             exclude: Iterable[str] = GL_EXCLUDE_PREFIXES) -> bool:
    """Whether a GL account belongs to marketing spend.

    Exclusion is evaluated FIRST and wins. That ordering is the point, not a
    detail: the NAF accounts (96212.*) mirror the marketing chart of accounts
    almost exactly, so any include pattern that is loosened - a substring
    match like '%6212%', or a shortened prefix like '6621' - silently pulls
    the GarageExperts franchisee fund into VHPC's marketing spend.

    The prefix sets are parameters so this precedence can be tested directly
    rather than assumed. See tests/test_spend.py::TestGLScope.
    """
    if any(account.startswith(p) for p in exclude):
        return False
    return any(account.startswith(p) for p in include)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class SpendData:
    year: int
    postings: dict[str, dict[str, Decimal]]
    corrections: list[dict]
    budget: dict
    _meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, year: int = 2026, actuals_period: str = "2026-08") -> "SpendData":
        act_path = REPO_ROOT / "data" / "snapshots" / actuals_period / "netsuite_marketing_spend.json"
        bud_path = REPO_ROOT / "data" / "manual" / str(year) / "approved_marketing_budget.json"
        actuals = json.loads(act_path.read_text())
        budget = json.loads(bud_path.read_text())

        postings: dict[str, dict[str, Decimal]] = {}
        for month, accts in actuals["postings"].items():
            for acct in accts:
                if not in_scope(acct):
                    raise ValueError(
                        f"snapshot {act_path.name} contains out-of-scope account {acct!r} "
                        f"for month {month}; marketing spend is {GL_INCLUDE_PREFIXES} "
                        f"excluding {GL_EXCLUDE_PREFIXES}"
                    )
            postings[month] = {a: _d(v) for a, v in accts.items()}

        return cls(
            year=year,
            postings=postings,
            corrections=actuals.get("corrections", []),
            budget=budget,
            _meta={"actuals": actuals["_meta"], "budget": budget["_meta"]},
        )

    # -- adjustments ------------------------------------------------------

    def _restatements(self) -> dict[str, Decimal]:
        """Per-month adjustments for current-year misbookings, keyed by month."""
        out: dict[str, Decimal] = {}
        for c in self.corrections:
            if not c.get("affects_monthly_measurement"):
                continue
            for month, amt in c.get("restates", {}).items():
                out[month] = out.get(month, Decimal(0)) + _d(amt)
        return out

    def _excluded_credits(self) -> dict[str, Decimal]:
        """Credits that must not touch monthly measurement, keyed by month."""
        out: dict[str, Decimal] = {}
        for c in self.corrections:
            if c.get("affects_monthly_measurement"):
                continue
            m = c["credit_month"]
            out[m] = out.get(m, Decimal(0)) + _d(c["credit_amount"])
        return out

    # -- the series -------------------------------------------------------

    def monthly(self, basis: Basis = Basis.TRUE_OPERATING) -> dict[str, Money]:
        """Total marketing spend per month on the given basis."""
        raw = {m: sum(v.values(), Decimal(0)) for m, v in self.postings.items()}

        if basis is Basis.AS_POSTED:
            adjusted = raw
        elif basis is Basis.ANNUAL_LEDGER:
            adjusted = raw
        elif basis is Basis.TRUE_OPERATING:
            restate = self._restatements()
            drop = self._excluded_credits()
            adjusted = {}
            for m, total in raw.items():
                # Remove the credit itself from the month it landed in, and
                # push current-year misbookings back to their real months.
                seo_credit = sum(
                    _d(c["credit_amount"])
                    for c in self.corrections
                    if c.get("affects_monthly_measurement") and c["credit_month"] == m
                )
                adjusted[m] = total + restate.get(m, Decimal(0)) - drop.get(m, Decimal(0)) - seo_credit
        else:  # pragma: no cover - Enum is exhaustive
            raise ValueError(basis)

        return {m: Money(v, m) for m, v in sorted(adjusted.items())}

    def window(self, start: str, end: str, basis: Basis = Basis.TRUE_OPERATING,
               label: str | None = None) -> Money:
        """Inclusive month-range total, e.g. window('2026-01', '2026-07')."""
        months = [m for m in self.monthly(basis) if start <= m <= end]
        if not months:
            raise ValueError(f"no months in range {start}..{end}")
        series = self.monthly(basis)
        total = sum((series[m].amount for m in months), Decimal(0))
        return Money(total, label or f"{start}..{end}")

    def by_account(self, start: str, end: str, reclass: bool = True) -> dict[str, Decimal]:
        """Actual spend per GL account over a month range.

        With reclass=True, accounts carrying a `reclass_to` in the budget file
        are folded into their target. That is what turns the pre-split
        Advertising catch-all back into Google for channel reporting.
        """
        mapping = {
            a: cfg["reclass_to"]
            for a, cfg in self.budget["accounts"].items()
            if cfg.get("reclass_to")
        } if reclass else {}

        out: dict[str, Decimal] = {}
        for month, accts in self.postings.items():
            if not (start <= month <= end):
                continue
            for acct, amt in accts.items():
                target = mapping.get(acct, acct)
                out[target] = out.get(target, Decimal(0)) + amt
        return out

    # -- budget -----------------------------------------------------------

    def budget_monthly(self, account: str, honour_cancellations: bool = True) -> list[Decimal]:
        cfg = self.budget["accounts"][account]
        vals = [_d(v) for v in cfg["monthly"]]
        if honour_cancellations:
            for c in self.budget.get("cancellations", []):
                if c["account"] != account:
                    continue
                for m in c["months_released"]:
                    idx = int(m.split("-")[1]) - 1
                    vals[idx] = Decimal(0)
        return vals

    def budget_window(self, start: str, end: str, honour_cancellations: bool = True) -> Money:
        lo, hi = int(start.split("-")[1]), int(end.split("-")[1])
        total = Decimal(0)
        for acct in self.budget["accounts"]:
            vals = self.budget_monthly(acct, honour_cancellations)
            total += sum(vals[lo - 1:hi], Decimal(0))
        return Money(total, f"{start}..{end}")

    def released_by_cancellation(self) -> Money:
        total = sum(
            (_d(c["amount_released"]) for c in self.budget.get("cancellations", [])),
            Decimal(0),
        )
        return Money(total, f"FY{self.year}")

    def budget_vs_actual(self, start: str, end: str,
                         honour_cancellations: bool = True) -> list[dict]:
        """One row per GL account. Every dollar of actual is represented.

        v1's table listed six of ten rows and under-reported actual spend by
        $41,777. Here the row set is the union of budgeted and posted
        accounts, so a posting with no budget line becomes a visible row
        instead of vanishing.
        """
        lo, hi = int(start.split("-")[1]), int(end.split("-")[1])
        actual = self.by_account(start, end, reclass=False)
        accounts = sorted(set(self.budget["accounts"]) | set(actual))

        rows = []
        for acct in accounts:
            cfg = self.budget["accounts"].get(acct, {})
            if acct in self.budget["accounts"]:
                bud = sum(self.budget_monthly(acct, honour_cancellations)[lo - 1:hi], Decimal(0))
            else:
                bud = Decimal(0)
            act = actual.get(acct, Decimal(0))
            if bud == 0 and act == 0:
                continue
            rows.append({
                "account": acct,
                "display": cfg.get("display", acct),
                "budget": Money(bud, f"{start}..{end}"),
                "actual": Money(act, f"{start}..{end}"),
                "variance": Money(act - bud, f"{start}..{end}"),
                "unbudgeted": bud == 0 and act != 0,
                "derived": bool(cfg.get("derived")),
                "note": cfg.get("note"),
            })
        return rows


# ---------------------------------------------------------------------------
# Efficiency
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Efficiency:
    period: str
    spend: Money
    m1_net_revenue: Money
    new_customers: int

    @property
    def return_per_dollar(self) -> Ratio:
        return Ratio(self.m1_net_revenue.amount / self.spend.amount)

    @property
    def cost_per_customer(self) -> Money:
        return Money(self.spend.amount / Decimal(self.new_customers), self.period)

    @property
    def spend_share_of_revenue(self) -> Decimal:
        return self.spend.amount / self.m1_net_revenue.amount * Decimal(100)

    @property
    def avg_first_order(self) -> Money:
        return Money(self.m1_net_revenue.amount / Decimal(self.new_customers), self.period)


def price_ask(monthly_amount, months: int, budget: dict,
              label: str = "ask") -> dict:
    """Price a paid-media budget ask including the derived agency surcharge.

    The approved budget computes agency fees as 20% of Google plus Meta. An
    ask for $1,000/month of Google therefore costs $1,200/month. v1 priced
    the September retargeting run at $3,000; the all-in figure is $3,600.
    """
    amt = _d(monthly_amount)
    formula = budget["derived_lines"]["66212.0002"]["formula"]
    rate = Decimal("0.20")  # kept in step with the formula string below
    if "0.20" not in formula and "0.2" not in formula:  # pragma: no cover
        raise ValueError(f"agency-fee formula changed, re-derive rate: {formula}")
    media = amt * months
    agency = media * rate
    return {
        "label": label,
        "media": Money(media, label),
        "agency_surcharge": Money(agency, label),
        "all_in": Money(media + agency, label),
        "monthly_all_in": Money(amt * (1 + rate), label),
        "months": months,
        "agency_rate": rate,
    }
