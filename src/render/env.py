"""The Jinja2 environment. Strict, autoescaped, and innumerate on purpose.

Three decisions, each a v1 lesson:

- `StrictUndefined`. A template that references a context key the build did
  not supply dies at render time instead of printing an empty string. v1's
  Sales page shipped a tile whose value was literally blank for a month.

- Autoescape on. Metric spans are `Markup` built by the registry, so they
  pass through untouched; anything else is text and is escaped.

- No number filters. There is no `|money`, no `|pct`, no `|round`. Numbers
  are formatted by src.units and enter a template already formatted, inside
  a data-metric span. The filters that exist here are for layout only. If
  you find yourself wanting a numeric filter, register a metric instead.

Chart payloads: `chart_json` serialises a chart_spec dict with Decimals
emitted as JSON numbers (not strings, not floats) and with <, > and &
escaped so the payload is safe inside a <script type="application/json">.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

from .brand import BRAND, TERMS
from .registry import MetricRegistry
from .sparkline import sparkline

__all__ = ["TEMPLATES", "make_env", "render", "chart_json", "term"]

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"

NAV = (
    {"href": "/executive", "label": "Executive", "slug": "executive"},
    {"href": "/marketing-ops", "label": "Marketing Ops", "slug": "marketing-ops"},
    {"href": "/sales", "label": "Sales", "slug": "sales"},
)

FLAG_ICONS = {"red": "🔴", "amber": "🟡", "green": "🟢", "blue": "🔵"}


# ---------------------------------------------------------------------------
# JSON for chart payloads
# ---------------------------------------------------------------------------

_HTML_SAFE = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026", ord("'"): "\\u0027"}


def chart_json(spec: Mapping[str, Any]) -> Markup:
    """Serialise a chart spec; Decimals become JSON numbers verbatim.

    json.dumps cannot emit a Decimal as a bare number without going through
    float, so each Decimal is swapped for a unique token and the tokens are
    replaced after serialisation. Exact digits in, exact digits out.
    """
    tokens: dict[str, str] = {}
    stamp = uuid.uuid4().hex

    def walk(o: Any) -> Any:
        if isinstance(o, Decimal):
            key = f"__DEC_{stamp}_{len(tokens)}__"
            tokens[key] = format(o, "f")
            return key
        if isinstance(o, float):
            raise TypeError("chart spec contains a float; every number is Decimal")
        if isinstance(o, dict):
            return {str(k): walk(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        return o

    text = json.dumps(walk(spec), ensure_ascii=False, separators=(",", ":"))
    if tokens:
        pattern = re.compile('"(' + "|".join(re.escape(k) for k in tokens) + ')"')
        text = pattern.sub(lambda m: tokens[m.group(1)], text)
    return Markup(text.translate(_HTML_SAFE))


def term(key: str) -> Markup:
    """A methodology term that legitimately contains digits, e.g. M1.

    Rendered as <dfn data-term="KEY">, which the bare-digit scan exempts by
    vocabulary. Unknown keys raise so a typo cannot smuggle a number in.
    """
    try:
        text = TERMS[key]
    except KeyError:
        raise KeyError(f"unknown term {key!r}; known terms: {sorted(TERMS)}") from None
    return Markup(f'<dfn class="term" data-term="{escape(key)}">{escape(text)}</dfn>')


# ---------------------------------------------------------------------------
# Layout-only filters
# ---------------------------------------------------------------------------

def _flag_icon(severity: str) -> str:
    try:
        return FLAG_ICONS[severity]
    except KeyError:
        raise ValueError(
            f"flag severity must be one of {sorted(FLAG_ICONS)}, not {severity!r}"
        ) from None


def _align_class(align: str | None) -> str:
    return {"right": "right", "center": "center"}.get(align or "left", "")


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

class _IdView:
    """`'x.y' in registered_ids` inside a template: is this id registered?

    Lets a template show a change only when the prior month's figure exists
    (a series that started this month has nothing to change from). Reads the
    live registry, so it never goes stale within a render."""

    def __init__(self, registry: MetricRegistry) -> None:
        self._r = registry

    def __contains__(self, metric_id: object) -> bool:
        return isinstance(metric_id, str) and metric_id in self._r.ids()


def make_env(registry: MetricRegistry, templates: Path = TEMPLATES) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates)),
        undefined=StrictUndefined,
        autoescape=select_autoescape(default_for_string=True, default=True),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals.update({
        # the registry surface - the only route for a figure into a page
        "m": registry.m,
        "c": registry.c,
        "registered_ids": _IdView(registry),
        "delta_between": registry.delta_between,
        "column_total": registry.total,
        # brand + layout
        "brand": BRAND,
        "term": term,
        "chart_json": chart_json,
        "spark": sparkline,
        "nav": NAV,
        "flag_icon": _flag_icon,
    })
    # Layout filters only. No numeric filter will ever be added here.
    env.filters["align_class"] = _align_class
    return env


def render(template_name: str, context: Mapping[str, Any], *,
           registry: MetricRegistry, templates: Path = TEMPLATES) -> str:
    """Render a page. Any undefined name, unknown metric, or failed claim raises."""
    env = make_env(registry, templates)
    tpl = env.get_template(template_name)
    return tpl.render(**context)
