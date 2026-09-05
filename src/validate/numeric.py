"""Parsing rendered figures back into Decimals.

Everything the dashboards print is a string by the time the gate sees it:
"$1,234", "(1,234)", "−$1,234", "1,234.56", "12.3%", "$51K", "1.52x". This
module turns those back into Decimal so the table-integrity and consistency
checks can do arithmetic on what the reader actually sees, not on what the
template thought it rendered.

Decimal, never float: the breakdown check compares column sums to a total
within $1, and a float sum of eleven currency cells is exactly the kind of
thing that lands at 136,890.99999.

Three minus signs are accepted (ASCII hyphen, U+2212 MINUS, en dash) because
v1 used all three - &minus; in Budget_Performance, a literal "−" in the
Pipeline deltas, and "-" in the JS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["ParsedNumber", "parse_number", "DIGIT_GROUP", "strip_decorations"]

CURRENCY = "currency"
COUNT = "count"
PCT = "pct"
MULTIPLE = "multiple"

# Footnote and emphasis glyphs v1 glued onto figures ("75 ⭐", "SEO ✻").
_DECORATIONS = "⭐★☆✻✱✽⚑*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰"

# A digit group as it appears in prose or a cell: 1,234 / 1234 / 12.5
DIGIT_GROUP = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

_MINUS = "[-−–]"
_NUM_RE = re.compile(
    rf"""^\s*
    (?P<sign1>{_MINUS}|\+)?\s*
    (?P<paren>\()?\s*
    (?P<sign2>{_MINUS})?\s*
    (?P<cur>\$)?\s*
    (?P<sign3>{_MINUS})?\s*
    (?P<num>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*(?P<suffix>[KkMm])?
    \s*(?P<pct>%)?
    \s*(?P<close>\))?
    \s*(?P<mult>[x×])?
    \s*$""",
    re.X,
)


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal
    kind: str            # currency | count | pct | multiple
    approx: bool         # True for K/M-suffixed figures, which are rounded
    text: str

    @property
    def decimals(self) -> int:
        """Displayed precision, for comparing a rendered figure to a raw one."""
        m = re.search(r"\.(\d+)", self.text)
        return len(m.group(1)) if m else 0


def strip_decorations(text: str) -> str:
    return text.strip().strip(_DECORATIONS).strip()


def parse_number(text: str) -> ParsedNumber | None:
    """The whole string must be one figure; "$0 direct" and "n/a" are None.

    Returning None for anything with trailing words is deliberate. A cell
    that says "$0 direct" is a label, and the table check treats labels as
    contributing nothing rather than guessing.
    """
    if text is None:
        return None
    s = strip_decorations(text)
    if not s:
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    value = Decimal(m.group("num").replace(",", ""))
    negative = bool(m.group("paren") or m.group("sign2") or m.group("sign3")
                    or (m.group("sign1") and m.group("sign1") != "+"))
    if m.group("paren") and not m.group("close"):
        return None
    suffix = (m.group("suffix") or "").upper()
    approx = False
    if suffix == "K":
        value *= 1000
        approx = True
    elif suffix == "M":
        value *= 1_000_000
        approx = True
    if negative:
        value = -value
    if m.group("pct"):
        kind = PCT
    elif m.group("cur"):
        kind = CURRENCY
    elif m.group("mult"):
        kind = MULTIPLE
    else:
        kind = COUNT
    return ParsedNumber(value, kind, approx, s)


def fmt(d: Decimal) -> str:
    """Human formatting for findings: thousands separators, no trailing zeros."""
    d = d.normalize() if d == d.to_integral() else d
    if d == d.to_integral():
        return f"{int(d):,}"
    return f"{d:,}"
