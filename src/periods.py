"""Period classification: which months are closed, which cohort windows are
complete, and what the reporting month is for a given as-of date.

Everything here is computed from a date. Nothing is a hardcoded month list -
v1 restricted the matched cohort comparison to "Jan-Apr" by hand, and the
list went stale the moment a month passed.

Two distinct questions, kept apart:

  calendar_closed(month, as_of)   the month is over
  m13_closed(cohort, as_of)       the cohort's first-90-days window is over

A cohort month can be calendar-closed for weeks while its 90-day window is
still open. Reporting a partial window as complete overstates weakness, so
anything M1-3 must check the second predicate, never the first.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

__all__ = [
    "M13_WINDOW_DAYS", "month_start", "month_end", "shift_month", "months_between",
    "calendar_closed", "m13_closed", "reporting_month", "rolling_window",
    "closed_m13_cohorts", "PeriodState", "classify",
]

M13_WINDOW_DAYS = 90   # the SQL is `trandate < datecreated + 90`; label it "first 90 days", not "3 months"


def _ym(month: str) -> tuple[int, int]:
    y, m = month.split("-")
    return int(y), int(m)


def month_start(month: str) -> date:
    y, m = _ym(month)
    return date(y, m, 1)


def month_end(month: str) -> date:
    y, m = _ym(month)
    return date(y, m, calendar.monthrange(y, m)[1])


def shift_month(month: str, n: int) -> str:
    y, m = _ym(month)
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def months_between(start: str, end: str) -> list[str]:
    """Inclusive list of 'YYYY-MM' from start to end."""
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = shift_month(cur, 1)
    return out


def calendar_closed(month: str, as_of: date) -> bool:
    return month_end(month) < as_of


def m13_closed(cohort_month: str, as_of: date) -> bool:
    """True once every customer created in the cohort month has had 90 days.

    The latest-created customer is created on the last day of the month, so
    the window closes at month_end + 90 days. Using month_start here would
    call a window closed a month early for late-month customers.
    """
    return month_end(cohort_month) + timedelta(days=M13_WINDOW_DAYS) <= as_of


def reporting_month(as_of: date) -> str:
    """The month being reported: the most recent calendar-closed month.

    The refresh runs on or near the 1st for the prior month, so on 2026-09-05
    the reporting month is 2026-08.
    """
    first_of_this = as_of.replace(day=1)
    prev_end = first_of_this - timedelta(days=1)
    return f"{prev_end.year:04d}-{prev_end.month:02d}"


def rolling_window(as_of: date, n: int = 12) -> list[str]:
    """The n calendar-closed months ending with the reporting month.

    v1's Leadership chart claimed "12 months ending July 31" and drew
    Jul 25 - Jun 26, silently omitting the reporting month. This always
    includes it.
    """
    end = reporting_month(as_of)
    return months_between(shift_month(end, -(n - 1)), end)


def closed_m13_cohorts(months: list[str], as_of: date) -> list[str]:
    return [m for m in months if m13_closed(m, as_of)]


class PeriodState(str, Enum):
    OPEN = "open"          # pull live on every build
    CLOSED = "closed"      # calendar over, not yet promoted - pull live, eligible to freeze
    FROZEN = "frozen"      # promoted snapshot; never overwritten by a live pull


@dataclass(frozen=True)
class Classification:
    month: str
    state: PeriodState
    calendar_closed: bool
    m13_closed: bool
    reason: str


def classify(month: str, as_of: date, frozen: bool) -> Classification:
    cc = calendar_closed(month, as_of)
    mc = m13_closed(month, as_of)
    if frozen:
        return Classification(month, PeriodState.FROZEN, cc, mc,
                              "promoted snapshot exists; read from disk, never overwrite")
    if not cc:
        return Classification(month, PeriodState.OPEN, cc, mc,
                              "calendar month not over; pull live")
    return Classification(month, PeriodState.CLOSED, cc, mc,
                          "calendar closed but not promoted; pull live, eligible for promotion")
