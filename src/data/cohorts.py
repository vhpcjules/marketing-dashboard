"""Cohort revenue: M1, repeat, and ROAS on both bases.

M1 revenue is the target frame, but it is not what marketing earned. A
customer acquired in March who reorders in June produces revenue that M1
never sees, and the M1 window closes before most reordering happens - the
median time to a second order is 17 days, but only 51% of eventual repeat
buyers have reordered by then, and 83% by day 90.

So this module carries two ROAS figures side by side:

    ROAS (M1)         M1 NET revenue / spend
    ROAS (to date)    all NET revenue from those cohorts / spend

The second is the truer measure of what the spend bought. The first is the
one that closes fast enough to steer on. Neither is complete on its own, and
the two must never be confused - which is why `Roas` refuses to exist without
a basis label and a maturity, in the same way `Money` refuses to exist
without a period.

Maturity is the whole caveat. A one-month-old cohort has produced 1.1x its
M1; an eight-month-old cohort has produced 3.3x. Comparing a young cohort's
to-date ROAS against an old one's is meaningless, so every aggregate reports
the weighted average maturity that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from ..units import Money, Ratio

__all__ = ["Cohort", "CohortSet", "Roas", "Basis"]


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class Roas:
    """Return on ad spend, inseparable from its basis and maturity.

    There is no such thing as "the" ROAS here. An M1 figure and a
    revenue-to-date figure differ by roughly 2x at four months of maturity
    and 5x at fourteen, so a ROAS quoted without both labels is a number
    waiting to be misread in a meeting.
    """

    value: Decimal
    basis: str            # "M1" or "revenue to date"
    period: str           # the acquisition window
    maturity_months: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _d(self.value))
        object.__setattr__(self, "maturity_months", _d(self.maturity_months))
        if not self.basis or not self.period:
            raise ValueError("Roas requires both a basis and an acquisition period")

    @property
    def per_dollar(self) -> str:
        return Ratio(self.value).per_dollar

    @property
    def label(self) -> str:
        return (f"{self.per_dollar} per $1 ({self.basis}, {self.period}, "
                f"{self.maturity_months.quantize(Decimal('0.1'))} months average maturity)")

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class Cohort:
    """One acquisition month."""

    month: str                 # 'YYYY-MM'
    customers: int
    m1_net: Decimal
    revenue_to_date: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "m1_net", _d(self.m1_net))
        object.__setattr__(self, "revenue_to_date", _d(self.revenue_to_date))
        if self.revenue_to_date < self.m1_net:
            raise ValueError(
                f"cohort {self.month}: revenue to date ({self.revenue_to_date}) is "
                f"below M1 ({self.m1_net}), which is impossible unless credits have "
                f"been applied outside the M1 window - investigate before publishing"
            )

    @property
    def repeat_revenue(self) -> Money:
        """Revenue beyond month one. What M1-only reporting throws away."""
        return Money(self.revenue_to_date - self.m1_net, f"{self.month} cohort, to date")

    @property
    def multiple(self) -> Ratio:
        return Ratio(self.revenue_to_date / self.m1_net)

    def maturity_months(self, as_of: date) -> int:
        y, m = (int(p) for p in self.month.split("-"))
        return (as_of.year - y) * 12 + (as_of.month - m)


@dataclass(frozen=True)
class CohortSet:
    """A group of acquisition months, with spend attached."""

    label: str
    cohorts: Sequence[Cohort]
    spend: Money
    as_of: date

    @property
    def customers(self) -> int:
        return sum(c.customers for c in self.cohorts)

    @property
    def m1_net(self) -> Money:
        return Money(sum((c.m1_net for c in self.cohorts), Decimal(0)), self.label)

    @property
    def revenue_to_date(self) -> Money:
        return Money(sum((c.revenue_to_date for c in self.cohorts), Decimal(0)), self.label)

    @property
    def repeat_revenue(self) -> Money:
        return Money(self.revenue_to_date.amount - self.m1_net.amount, self.label)

    @property
    def repeat_share(self) -> Decimal:
        """Repeat revenue as a share of everything these cohorts have produced."""
        return self.repeat_revenue.amount / self.revenue_to_date.amount * Decimal(100)

    @property
    def avg_maturity_months(self) -> Decimal:
        """Customer-weighted, not month-weighted.

        Month-weighting would let a 12-customer month pull the average as hard
        as a 127-customer month.
        """
        total = sum(c.maturity_months(self.as_of) * c.customers for c in self.cohorts)
        return _d(total) / _d(self.customers)

    @property
    def roas_m1(self) -> Roas:
        return Roas(self.m1_net.amount / self.spend.amount, "M1",
                    self.label, self.avg_maturity_months)

    @property
    def roas_to_date(self) -> Roas:
        return Roas(self.revenue_to_date.amount / self.spend.amount, "revenue to date",
                    self.label, self.avg_maturity_months)

    @property
    def m1_per_customer(self) -> Money:
        return Money(self.m1_net.amount / _d(self.customers), self.label)

    @property
    def to_date_per_customer(self) -> Money:
        return Money(self.revenue_to_date.amount / _d(self.customers), self.label)

    def understatement_of_m1_roas(self) -> Decimal:
        """How much of realised revenue the M1 frame does not see, as a share.

        At Jan-Aug 2026 this is about 46% - judging marketing on M1 alone
        credits it with roughly half of what its cohorts have already
        produced.
        """
        return self.repeat_share
