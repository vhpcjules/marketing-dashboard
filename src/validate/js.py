"""Reading the inline scripts without a JavaScript parser.

Chart data in these pages is declared as top-level array literals -
`const LI_NEW_FOLLOWERS = [6, 6, 74, ...]` - and consumed by `new Chart(...)`
calls. Regex over that shape is enough to answer the questions the gate
asks (what does the chart plot, what is the axis ceiling, which canvas does
it draw on) and a real parser would be a dependency the build cannot carry.

The limits are documented per function. When the shape is not recognised,
the answer is "no data", never a guess - a chart the gate cannot read is not
a chart the gate has approved, and the canvas-binding check still applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .dom import Node

__all__ = ["inline_scripts", "external_scripts", "numeric_arrays", "string_arrays",
           "chart_blocks", "ChartBlock", "style_text"]

_ARRAY_RE = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\[([^\[\]]*)\]", re.S)
_NUM_ITEM = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_STR_ITEM = re.compile(r"""^(?:'([^']*)'|"([^"]*)"|`([^`]*)`)$""")


def inline_scripts(doc: Node) -> list[tuple[Node, str]]:
    return [(s, s.raw_text()) for s in doc.find_all("script") if not s.get("src")]


def external_scripts(doc: Node) -> list[Node]:
    return [s for s in doc.find_all("script") if s.get("src")]


def style_text(doc: Node) -> str:
    return "\n".join(s.raw_text() for s in doc.find_all("style"))


def _split_items(body: str) -> list[str]:
    body = re.sub(r"//[^\n]*", "", body)          # trailing comments inside arrays
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return [i.strip() for i in body.split(",") if i.strip()]


def numeric_arrays(script: str) -> dict[str, list[Decimal | None]]:
    """Top-level numeric array literals. `null`/`undefined`/NaN items are None.

    Only flat arrays are read; nested literals and computed arrays are
    skipped because their contents cannot be known statically.
    """
    out: dict[str, list[Decimal | None]] = {}
    for m in _ARRAY_RE.finditer(script):
        name, body = m.group(1), m.group(2)
        items = _split_items(body)
        if not items:
            continue
        vals: list[Decimal | None] = []
        ok = True
        for it in items:
            if it in ("null", "undefined", "NaN"):
                vals.append(None)
            elif _NUM_ITEM.match(it):
                try:
                    vals.append(Decimal(it))
                except InvalidOperation:  # pragma: no cover
                    ok = False
                    break
            else:
                ok = False
                break
        if ok:
            out[name] = vals
    return out


def string_arrays(script: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in _ARRAY_RE.finditer(script):
        name, body = m.group(1), m.group(2)
        items = _split_items(body)
        if not items:
            continue
        vals = []
        for it in items:
            sm = _STR_ITEM.match(it)
            if not sm:
                break
            vals.append(next(g for g in sm.groups() if g is not None))
        else:
            out[name] = vals
    return out


@dataclass(frozen=True)
class ChartBlock:
    canvas_ref: str | None   # the getElementById argument, or None if indirect
    source: str              # the text of the `new Chart(...)` block
    line: int                # line within the script


_CHART_START = re.compile(r"new\s+Chart\s*\(")


def _balanced_span(text: str, open_idx: int) -> int:
    """Index just past the ')' matching the '(' at open_idx, or len(text)."""
    depth = 0
    in_str: str | None = None
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "'\"`":
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def chart_blocks(script: str) -> list[ChartBlock]:
    """Every `new Chart(` call with its full argument text."""
    blocks = []
    for m in _CHART_START.finditer(script):
        start = m.start()
        end = _balanced_span(script, m.end() - 1)
        src = script[start:end]
        ref = re.search(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", src[:200])
        blocks.append(ChartBlock(ref.group(1) if ref else None, src,
                                 script.count("\n", 0, start) + 1))
    return blocks
