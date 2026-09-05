"""Prose guards for OUR templates: no orphaned numbers, no stale months.

The v1 pages were hand-written prose with figures typed into them. Every
sentence with a number in it was a place the number could go stale, and
several did: Leadership's static explainer still read "Latest month (May
2026) vs. previous month (April 2026)" on a July page, and a Pipeline tile
compared "vs April" while its neighbours compared "vs June".

The rule for our templates is mechanical: a digit that reaches a reader
must come from a data-metric (a figure the build computed) or a data-claim
(a statement the build derived). Anything else is a typed number, and typed
numbers are what rot. The allowlist is for things that are labels rather
than figures - years, dates, footnote markers, "M1"/"M1-3" window names and
"first 90 days" style window labels - and every entry says why.

Month names are a warning, not a failure, because prose legitimately names
months ("since January the mix has shifted"). The warning exists so that a
"vs April" left behind on an August page is read by a human before it is
read by leadership.
"""

from __future__ import annotations

import re

from .dom import Node
from .findings import WARN, Finding
from .months import find_month_mentions, month_names_for
from .numeric import DIGIT_GROUP

__all__ = ["check_orphaned_numbers", "check_stale_months", "PROSE_TAGS"]

# Text under these tags is prose a reader takes as a statement of fact.
PROSE_TAGS = frozenset({"p", "li", "td", "span"})

# Text under these is never a figure.
SKIP_TAGS = frozenset({"sup", "code", "pre", "time", "kbd", "var", "th"})

_MONTH_ALT = (r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?")

# Each pattern is matched against the text with the digit group's span, and
# the group is allowed if any pattern's match covers it.
ALLOW = [
    # years: 2025, FY2026, '26 after a month name
    (re.compile(r"\b(?:FY\s?)?(?:19|20)\d\d\b"), "year"),
    (re.compile(rf"\b{_MONTH_ALT}\s*'?\d\d\b"), "month + 2-digit year"),
    # dates: 2026-08-05, 2026-08, 8/5/2026, 8/5, Aug 5, 5 Aug, August 5th
    (re.compile(r"\b(?:19|20)\d\d-\d\d(?:-\d\d)?\b"), "ISO date"),
    (re.compile(r"\b\d{1,2}/\d{1,2}(?:/(?:\d\d|\d{4}))?\b"), "slash date"),
    (re.compile(rf"\b{_MONTH_ALT}\s+\d{{1,2}}(?:st|nd|rd|th)?\b"), "month day"),
    (re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH_ALT}"), "day month"),
    # footnote markers: [1], (1), ¹ is not a \d so never matched
    (re.compile(r"[\[(]\d{1,2}[\])]"), "footnote marker"),
    # window / cohort labels: M1, M1-3, M1–3, Q3, H1
    (re.compile(r"\b[MQH]\d{1,2}(?:\s*[-–]\s*\d{1,2})?\b"), "window label"),
    # window lengths are labels, not figures: "first 90 days", "12-mo avg", "6-month"
    (re.compile(r"\b\d{1,3}[\s-](?:mo|month|day|week|yr|year)s?\b", re.I), "window length label"),
    # ordinal list markers and times: "1.", "3rd", "10:30"
    (re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b"), "ordinal"),
    (re.compile(r"\b\d{1,2}:\d\d\b"), "time"),
]


def _allowed(text: str, start: int, end: int) -> str | None:
    for pat, why in ALLOW:
        for m in pat.finditer(text):
            if m.start() <= start and m.end() >= end:
                return why
    return None


def check_orphaned_numbers(doc: Node, file: str) -> list[Finding]:
    """Every digit group in prose must sit under data-metric or data-claim."""
    out = []
    for tn in doc.text_nodes():
        if not any(ch.isdigit() for ch in tn.text):
            continue
        parent = tn.parent
        if parent is None or not parent.has_ancestor_tag(PROSE_TAGS):
            continue
        if parent.has_ancestor_tag(SKIP_TAGS):
            continue
        if (parent.has_ancestor_attr("data-metric") or parent.has_ancestor_attr("data-claim")
                or parent.has_ancestor_attr("data-period")):
            continue
        # A quotation of a RETIRED finding ("v1 said $33,177 under") is not a
        # statement of fact about this month. The narrative layer marks the
        # not-carried-forward list data-retired; nothing else may use it.
        if parent.has_ancestor_attr("data-retired"):
            continue
        text = " ".join(tn.text.split())
        for m in DIGIT_GROUP.finditer(text):
            if _allowed(text, m.start(), m.end()):
                continue
            out.append(Finding(
                "narrative.orphaned_number", file,
                f"typed number {m.group(0)!r} in <{parent.tag}> is not inside a data-metric or "
                f"data-claim element - it cannot be traced to the build and will go stale",
                evidence=_around(text, m.start(), m.end()),
            ))
    return out


def _around(text: str, start: int, end: int, width: int = 50) -> str:
    lo, hi = max(0, start - width), min(len(text), end + width)
    return ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")


def _prior(period: str) -> str:
    y, m = (int(p) for p in period.split("-"))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


def check_stale_months(doc: Node, file: str, reporting_period: str) -> list[Finding]:
    """Month names in prose that are neither the reporting nor the prior month.

    Text under an element with data-period, or inside <time>, is a deliberate
    label and is skipped; everything else is a warning so the human reads it. Table cells
    and headings are included because that is where "May" survived in v1.
    """
    out = []
    allowed_nums = {int(reporting_period.split("-")[1]), int(_prior(reporting_period).split("-")[1])}
    allowed = set()
    for n in allowed_nums:
        allowed |= month_names_for(n)
    for tn in doc.text_nodes():
        parent = tn.parent
        if parent is None or parent.has_ancestor_attr("data-period"):
            continue
        if parent.has_ancestor_tag({"time"}):
            continue        # <time> is a date by construction; the month name is the label, not a comparison
        text = " ".join(tn.text.split())
        for name, num, off in find_month_mentions(text):
            if name in allowed:
                continue
            out.append(Finding(
                "narrative.stale_month", file,
                f"{name!r} appears in prose but the reporting period is {reporting_period} "
                f"(prior {_prior(reporting_period)}); check this is not a left-over comparison",
                evidence=_around(text, off, off + len(name)),
                severity=WARN,
            ))
    return out
