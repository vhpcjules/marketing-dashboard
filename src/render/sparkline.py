"""Inline SVG sparklines, drawn by the build.

A sparkline is a trend cue, not a figure: it carries no axis, no tick and no
text, so nothing in it can be read as a number or go stale. The numbers the
line summarises sit next to it in the tile (a data-metric) and below it in
the twelve-month record table, both traceable. Drawing the line in Python
means the page needs no script for it, it prints, and the same Decimal
values that fed the table feed the line - there is no second copy.

`None` in the series is a gap (a month without the figure) and breaks the
line rather than pulling it to zero; v1 drew missing months as zero and one
of them read as a collapse.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from markupsafe import Markup

__all__ = ["sparkline"]

WIDTH, HEIGHT, PAD = 120, 32, 3


def _f(x: Decimal | float) -> str:
    return f"{float(x):.1f}".rstrip("0").rstrip(".")


def sparkline(values: Sequence[Decimal | int | None], emphasis: int | None = None, *,
              width: int = WIDTH, height: int = HEIGHT) -> Markup:
    """An SVG polyline over `values`, with one dot on `emphasis` if given.

    Returns an empty Markup when fewer than two points exist - a single
    point is not a trend and drawing one would imply otherwise.
    """
    pts = [(i, Decimal(v)) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return Markup("")
    n = len(values)
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    span = hi - lo
    inner_w, inner_h = width - 2 * PAD, height - 2 * PAD

    def xy(i: int, v: Decimal) -> tuple[Decimal, Decimal]:
        x = Decimal(PAD) + (Decimal(i) / Decimal(n - 1)) * inner_w if n > 1 else Decimal(width) / 2
        y = Decimal(PAD) + Decimal(inner_h) / 2 if span == 0 else Decimal(PAD) + (1 - (v - lo) / span) * inner_h
        return x, y

    # Consecutive present points join; a None between two points breaks the line.
    segments: list[list[str]] = []
    prev_i = None
    for i, v in pts:
        x, y = xy(i, v)
        if prev_i is None or i != prev_i + 1:
            segments.append([])
        segments[-1].append(f"{_f(x)},{_f(y)}")
        prev_i = i
    paths = "".join(f'<polyline points="{" ".join(seg)}"/>' for seg in segments if len(seg) > 1)
    dot = ""
    if emphasis is not None and 0 <= emphasis < n and values[emphasis] is not None:
        x, y = xy(emphasis, Decimal(values[emphasis]))
        dot = f'<circle class="spark-dot" cx="{_f(x)}" cy="{_f(y)}" r="3"/>'
    return Markup(
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'aria-hidden="true" focusable="false">{paths}{dot}</svg>'
    )
