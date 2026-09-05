"""Words and labels: the rendered-text rules.

These run over RENDERED TEXT NODES ONLY - never over the raw HTML. That is
not a performance choice. A substring or raw-source match for "pt" hits
"phone caPTure" in a heading, "font-size:12pt" in a style attribute, and a
match for "pp" hits "oPPortunity" in a data attribute; "points" matches
"pointer-events". Word-boundary regexes over what a reader actually sees
have none of those problems, and dom.Node.text_nodes() already excludes
<script>, <style> and every attribute.

Rules:
  forbidden_term    pts / pp / percentage points / points -> a point
                    difference was published (v1 did this three times);
                    "gross" -> there is no gross figure anywhere.
  currency_period   every data-kind="currency" element carries a data-period
                    (on itself or an ancestor). Money() cannot be built
                    without one; this is the same rule at the HTML layer.
  delta_direction   class delta-good/delta-bad must agree with the sign of
                    data-delta, computed by the same direction_class() the
                    templates use. v1 styled a 63% fall green.
  percent_point     a percent-labelled delta that equals the raw difference
                    of two percentages nearby: "54% ... 12-mo avg 42% +12%".
                    54-42 = 12, but the relative change is +28.6%.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..units import UndefinedDeltaError, delta, direction_class
from .dom import Node
from .findings import WARN, Finding
from .numbers import TILE_CLASS

__all__ = ["check_forbidden_terms", "check_currency_period", "check_delta_direction",
           "check_percent_point_deltas", "FORBIDDEN"]

# (pattern, what it means). Order matters only for the message: the first
# match in a text node names the finding.
FORBIDDEN = [
    (re.compile(r"\bpercentage[\s-]points?\b", re.I), "a percentage-point difference"),
    (re.compile(r"\bpts?\b", re.I), "a points abbreviation ('pt'/'pts')"),
    (re.compile(r"\bpp\b", re.I), "a percentage-point abbreviation ('pp')"),
    (re.compile(r"\bpoints?\b", re.I), "the word 'point(s)' - state a range or a relative change"),
    (re.compile(r"\bgross\b", re.I), "'gross' - there is no gross figure anywhere in the output"),
]


def check_forbidden_terms(doc: Node, file: str) -> list[Finding]:
    out = []
    for tn in doc.text_nodes():
        text = " ".join(tn.text.split())
        if not text:
            continue
        for pat, why in FORBIDDEN:
            m = pat.search(text)
            if m:
                out.append(Finding("language.forbidden_term", file,
                                   f"rendered text contains {m.group(0)!r}: {why}",
                                   evidence=_around(text, m.start(), m.end())))
                break
    return out


def _around(text: str, start: int, end: int, width: int = 60) -> str:
    lo, hi = max(0, start - width), min(len(text), end + width)
    return ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")


def check_currency_period(doc: Node, file: str) -> list[Finding]:
    out = []
    for n in doc.find_all(attr="data-kind"):
        if n.get("data-kind") != "currency":
            continue
        if not n.has_ancestor_attr("data-period"):
            out.append(Finding("language.currency_period", file,
                               "currency figure has no data-period label on itself or an ancestor "
                               "(M1, first-90-days and lifetime figures are not interchangeable)",
                               evidence=f"<{n.tag} data-metric={n.get('data-metric')!r}> {n.rendered_text()!r} line {n.line}"))
    return out


def check_delta_direction(doc: Node, file: str) -> list[Finding]:
    out = []
    for n in doc.elements():
        cls = n.classes()
        stated = {c for c in cls if c in ("delta-good", "delta-bad", "delta-flat")}
        if not stated:
            continue
        raw = n.get("data-delta")
        if raw is None:
            if stated - {"delta-flat"}:
                out.append(Finding("language.delta_direction", file,
                                   f"element styled {sorted(stated)} has no data-delta, so its colour cannot be verified",
                                   evidence=f"<{n.tag}> {n.rendered_text()!r} line {n.line}", severity=WARN))
            continue
        try:
            change = Decimal(raw.replace("+", "").replace("−", "-").strip())
        except InvalidOperation:
            out.append(Finding("language.delta_direction", file,
                               f"data-delta {raw!r} is not a number", evidence=f"<{n.tag}> line {n.line}"))
            continue
        hib = (n.get("data-higher-is-better") or "true").strip().lower() not in ("false", "0", "no")
        expected = direction_class(change, higher_is_better=hib)
        if expected not in stated:
            out.append(Finding("language.delta_direction", file,
                               f"delta {raw} with higher-is-better={hib} should be styled "
                               f"{expected!r} but carries {sorted(stated)}",
                               evidence=f"<{n.tag}> {n.rendered_text()!r} line {n.line}"))
    return out


# ---------------------------------------------------------------------------
# Percent-labelled point differences
# ---------------------------------------------------------------------------

_PCT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")
_DELTA_TEXT = re.compile(r"^\s*(?:[↑↓→]\s*)?([+−\-])?\s*(\d+(?:\.\d+)?)\s*%\s*(?:vs\b.*)?$")
# "45.6% → 55.7% (+10.1%)" and "from 36.9% to 52.0%, +15.1%"
_INLINE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:→|->|to|vs\.?|versus|against)\s*(\d+(?:\.\d+)?)\s*%[^%\d]{0,12}?"
    r"\(?\s*([+−\-]|up|down)?\s*(\d+(?:\.\d+)?)\s*%",
    re.I,
)


def _q(x: Decimal, decimals: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _is_point_difference(a: Decimal, b: Decimal, d: Decimal, decimals: int) -> bool:
    """d equals |a-b| and is NOT the relative change - i.e. a point difference
    wearing a percent sign. Both conditions, so a coincidence like
    100% -> 200% (+100 points, +100%) is not flagged."""
    if a == b:
        return False
    if _q(abs(a - b), decimals) != d:
        return False
    for cur, prev in ((a, b), (b, a)):
        try:
            if _q(abs(delta(cur, prev)), decimals) == d:
                return False
        except UndefinedDeltaError:
            continue
    return True


def check_percent_point_deltas(doc: Node, file: str) -> list[Finding]:
    out: list[Finding] = []
    seen: set[tuple] = set()

    # 1. A delta element next to two percentages in the same tile.
    for n in doc.elements():
        if not any(c == "delta" or c.startswith("delta-") for c in n.classes()):
            continue
        m = _DELTA_TEXT.match(n.rendered_text())
        if not m:
            continue
        d_text = m.group(2)
        d = Decimal(d_text)
        decimals = len(d_text.split(".")[1]) if "." in d_text else 0
        # A delta in a table cell compares against its own row; anywhere
        # else, against the tile it sits in. Without the row rule the whole
        # table is one container and every pair of percentages across rows
        # becomes a candidate (YoY's compare table produced four false hits).
        container = n.closest(lambda x: x is not n and x.tag == "tr")
        if container is None:
            container = n.closest(lambda x: x is not n and bool(TILE_CLASS.search(x.get("class") or "")))
        if container is None:
            container = n.parent.parent if n.parent and n.parent.parent else n.parent
        if container is None:
            continue
        own = n.rendered_text()
        others = [Decimal(v) for v in _PCT.findall(container.rendered_text().replace(own, " ", 1))]
        for i, a in enumerate(others):
            for b in others[i + 1:]:
                if _is_point_difference(a, b, d, decimals):
                    key = (container.line, str(a), str(b), str(d))
                    if key in seen:
                        continue
                    seen.add(key)
                    rel = _q(delta(max(a, b), min(a, b)), 1)
                    out.append(Finding(
                        "language.percent_point_delta", file,
                        f"'{own.strip()}' is the raw difference of {a}% and {b}% printed with a percent "
                        f"sign; the relative change is {rel}% (the % sign on a point difference "
                        f"is the v1 phone-capture bug)",
                        evidence=container.rendered_text(),
                    ))

    # 2. Inline prose: "45.6% → 55.7% (+10.1%)".
    for tn in doc.text_nodes():
        text = " ".join(tn.text.split())
        for m in _INLINE.finditer(text):
            a, b, d_text = Decimal(m.group(1)), Decimal(m.group(2)), m.group(4)
            d = Decimal(d_text)
            decimals = len(d_text.split(".")[1]) if "." in d_text else 0
            if _is_point_difference(a, b, d, decimals):
                key = ("inline", tn.line, m.group(0))
                if key in seen:
                    continue
                seen.add(key)
                rel = _q(delta(b, a), 1)
                out.append(Finding(
                    "language.percent_point_delta", file,
                    f"'{m.group(0)}' prints the point difference {d} with a percent sign; "
                    f"the relative change {a}% -> {b}% is {rel:+}%",
                    evidence=_around(text, m.start(), m.end()),
                ))
    return out
