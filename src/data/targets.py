"""Revenue targets and pace against them.

The target is set as growth over the prior year on the M1 NET revenue basis.
Both sides are measured on the CURRENT data basis, not against previously
published figures: comparing today's 2026 to August's 2025 would mix bases
and flatter or punish the result by whatever has drifted in between.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..units import Money, Ratio, delta

REPO_ROOT = Path(__file__).resolve().parents[2]


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class Pace:
    """Where we stand against a full-year target, and what it would take."""

    target: Money
    actual_to_date: Money
    months_elapsed: int
    months_remaining: int
    prior_year_same_remainder: Money
    run_rate: Money

    @property
    def still_needed(self) -> Money:
        return Money(self.target.amount - self.actual_to_date.amount, self.target.period)

    @property
    def required_monthly(self) -> Money:
        return Money(self.still_needed.amount / self.months_remaining, self.target.period)

    @property
    def required_uplift_vs_prior_year(self) -> Decimal:
        """How much the remaining months must beat the same months last year."""
        return delta(self.still_needed.amount, self.prior_year_same_remainder.amount)

    @property
    def forecast_at_run_rate(self) -> Money:
        return Money(
            self.actual_to_date.amount + self.run_rate.amount * self.months_remaining,
            self.target.period,
        )

    @property
    def gap_at_run_rate(self) -> Money:
        return Money(self.target.amount - self.forecast_at_run_rate.amount, self.target.period)

    @property
    def on_track(self) -> bool:
        return self.gap_at_run_rate.amount <= 0

    def spend_to_close_gap(self, return_per_dollar) -> Money:
        """Extra spend needed to close the gap at a stated return per dollar.

        Deliberately takes the return as an argument rather than assuming the
        historical average. Marginal return is below average return, so
        quoting the average here would understate what closing the gap costs.
        """
        r = _d(return_per_dollar)
        if r <= 0:
            raise ValueError("return per dollar must be positive")
        return Money(self.gap_at_run_rate.amount / r, self.target.period)


def load_target(year: int = 2026) -> dict:
    p = REPO_ROOT / "data" / "manual" / str(year) / "approved_marketing_budget.json"
    t = json.loads(p.read_text())["targets"]
    if t.get("target_amount") is None:
        raise ValueError(f"no revenue target set for {year}")
    return t
