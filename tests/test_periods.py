"""Period classification is computed, never listed."""
from datetime import date
import pytest
from src.periods import (calendar_closed, m13_closed, reporting_month, rolling_window,
                         closed_m13_cohorts, months_between, shift_month, classify, PeriodState)

SEP5 = date(2026, 9, 5)


def test_reporting_month_is_the_prior_calendar_month():
    assert reporting_month(SEP5) == "2026-08"
    assert reporting_month(date(2026, 1, 1)) == "2025-12"
    assert reporting_month(date(2026, 8, 31)) == "2026-07"


def test_rolling_window_includes_the_reporting_month():
    """v1 drew Jul25-Jun26 under a header claiming 'ending July 31'."""
    w = rolling_window(SEP5, 12)
    assert w[0] == "2025-09" and w[-1] == "2026-08" and len(w) == 12


def test_calendar_closed():
    assert calendar_closed("2026-08", SEP5)
    assert not calendar_closed("2026-09", SEP5)
    assert not calendar_closed("2026-08", date(2026, 8, 31))


class TestM13Window:
    """Closes at month END + 90 days, so the last-created customer has had 90 days."""

    def test_april_closed_by_early_august(self):
        # 2026-04-30 + 90d = 2026-07-29
        assert m13_closed("2026-04", date(2026, 7, 29))
        assert not m13_closed("2026-04", date(2026, 7, 28))

    def test_may_and_june_not_closed_on_sep_5(self):
        # 2026-05-31 + 90d = 2026-08-29 -> closed; 2026-06-30 + 90d = 2026-09-28 -> open
        assert m13_closed("2026-05", SEP5)
        assert not m13_closed("2026-06", SEP5)

    def test_closed_cohorts_are_computed_not_listed(self):
        months = months_between("2026-01", "2026-08")
        assert closed_m13_cohorts(months, SEP5) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]

    def test_month_start_would_be_wrong(self):
        """Using the 1st would call June closed on Aug 30 - a month early for a
        customer created June 30."""
        assert not m13_closed("2026-06", date(2026, 8, 30))


def test_shift_and_between():
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2025-12", 1) == "2026-01"
    assert months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_classify_states():
    assert classify("2026-09", SEP5, frozen=False).state is PeriodState.OPEN
    assert classify("2026-08", SEP5, frozen=False).state is PeriodState.CLOSED
    assert classify("2026-07", SEP5, frozen=True).state is PeriodState.FROZEN
