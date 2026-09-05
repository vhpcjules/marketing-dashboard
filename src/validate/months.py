"""Month names as they appear in prose, labels, and JS label arrays.

Used by two checks that must agree with each other: the stale-month warning
in narrative.py ("vs April" left behind in a July page) and the table-vs-
array comparison in numbers.py ('Mar 2026' in a <td> against 'Mar 26' in a
Chart.js labels array).

"May" is the trap. It is a month and a modal verb, and a case-insensitive
match on it fires on every "this may indicate". So month matching is
case-sensitive, and "May" additionally needs a year, a day, or a preposition
next to it before it counts as a month.
"""

from __future__ import annotations

import re

__all__ = ["MONTHS", "ABBR", "month_number", "parse_month_label", "MONTH_WORD_RE",
           "month_names_for", "find_month_mentions"]

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
ABBR = [m[:3] for m in MONTHS]

_LOOKUP = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
_LOOKUP.update({a.lower(): i + 1 for i, a in enumerate(ABBR)})
_LOOKUP["sept"] = 9

# Capitalised only: "march" the verb and "may" the modal are not months.
_NAMES_ALT = "|".join(sorted(set(MONTHS + ABBR + ["Sept"]), key=len, reverse=True))
MONTH_WORD_RE = re.compile(rf"\b(?P<name>{_NAMES_ALT})\b\.?")

# What has to sit next to "May" for it to be the month.
_MAY_CONTEXT_BEFORE = re.compile(r"(?:\b(?:vs\.?|in|since|from|to|of|for|through|until|and|–|-|→)\s*|\(\s*)$")
_MAY_CONTEXT_AFTER = re.compile(r"^\s*(?:\d{1,2}(?:st|nd|rd|th)?\b|20\d\d\b|'\d\d\b|\d\d\b|→|–|-|vs\.?|to\b|and\b)")


def month_number(name: str) -> int | None:
    return _LOOKUP.get(name.rstrip(".").lower())


def month_names_for(month_num: int) -> set[str]:
    return {MONTHS[month_num - 1], ABBR[month_num - 1]} | ({"Sept"} if month_num == 9 else set())


def parse_month_label(text: str) -> tuple[int, int | None] | None:
    """'Mar 2026' -> (3, 2026); 'Mar 26' -> (3, 2026); 'Aug 26*' -> (8, 2026);
    'March' -> (3, None). Anything else -> None."""
    s = re.sub(r"[^\w\s'/-]", " ", text).strip()
    m = re.match(rf"^(?P<name>{_NAMES_ALT}|[A-Za-z]+)\.?\s*[-'/]?\s*(?P<year>\d{{2}}|\d{{4}})?\s*$", s)
    if not m:
        return None
    num = month_number(m.group("name"))
    if num is None:
        return None
    year = m.group("year")
    if year is None:
        return (num, None)
    y = int(year)
    if y < 100:
        y += 2000
    return (num, y)


def find_month_mentions(text: str) -> list[tuple[str, int, int]]:
    """[(name, month_number, offset)] for every month word in prose."""
    out = []
    for m in MONTH_WORD_RE.finditer(text):
        name = m.group("name")
        if name == "May":
            before = text[: m.start()]
            after = text[m.end():]
            if not (_MAY_CONTEXT_BEFORE.search(before) or _MAY_CONTEXT_AFTER.match(after)):
                continue
        out.append((name, month_number(name), m.start()))
    return out
