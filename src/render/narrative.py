"""Narrative content: the monthly story, written once, resolved by the build.

Numbers are computed; the story is written in content/YYYY-MM/<dashboard>.md
and reviewed. Prose never contains a literal number. Every figure in it is a
reference the data layer resolves, so a removed or renamed metric fails the
build loudly instead of leaving stale digits behind (content/README.md).

Pipeline, in order:

  1. Front-matter (YAML) is split from the Markdown body. It declares the
     period and dashboard the file belongs to, `claims` (derived statements
     with an assertion), and `not_carried_forward` (v1 findings retired
     this month, with the reason - nothing is dropped silently).
  2. Claims are registered on the page's MetricRegistry. A claim's `expr`
     is arithmetic over metric ids ("fy25.a / fy25.b", "delta(x, y)") and
     nothing else: it is parsed with `ast` and evaluated by a walker that
     accepts + - * / unary minus, numeric constants, dotted metric ids and
     the functions delta/abs/min/max. There is no eval().
  3. The body is rendered through the same strict Jinja2 environment as the
     templates, so `{{ m("id") }}`, `{{ d("cur", "prev") }}` and
     `{{ c("claim") }}` resolve to traceable spans and a typo in an id is a
     build failure.
  4. The result is converted from Markdown to HTML and split at each `##`
     heading into sections keyed by slug. Templates place each section where
     it belongs (`narrative.section('are-we-growing')`). A section the
     template never asked for is reported by `unplaced()`, and the build
     refuses the page: prose that was written must be shown or deliberately
     removed, never lost in a slot nobody wired.

A missing content file is not an error - the numbers are refreshed on their
own schedule and the story is written after them - but it is visible: the
page carries a "narrative pending" callout instead of prose.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
from markupsafe import Markup, escape

from ..units import delta
from .env import make_env
from .registry import MetricRegistry, _raw

__all__ = ["CONTENT", "Narrative", "RenderedNarrative", "NarrativeError", "ClaimExprError",
           "load_narrative", "evaluate", "slugify", "markdown_to_html"]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT = REPO_ROOT / "content"

_FRONT = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)
_H1 = re.compile(r"^\s*<h1[^>]*>.*?</h1>\s*", re.S)
_H2_SPLIT = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_ASSERT = re.compile(r"^\s*(positive|negative|nonzero|between|at_least|at_most)\s*(?:\(\s*([^)]*)\))?\s*$")


class NarrativeError(ValueError):
    """The content file is malformed, or belongs to a different page or month."""


class ClaimExprError(ValueError):
    """A claim expression used something the evaluator does not allow."""


def slugify(text: str) -> str:
    plain = _TAGS.sub("", text)
    plain = re.sub(r"&[a-z]+;|&#\d+;", " ", plain)
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


# ---------------------------------------------------------------------------
# Claim expressions
# ---------------------------------------------------------------------------

_FUNCS: dict[str, Callable[..., Any]] = {
    "delta": delta,
    "abs": abs,
    "min": min,
    "max": max,
}


def _dotted(node: ast.AST) -> str | None:
    """'fy25.vintage_avg' parses as Attribute(Name); flatten it back to an id."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def evaluate(expr: str, lookup: Callable[[str], Decimal]) -> Decimal:
    """Evaluate a claim expression over metric values. No eval(), no names
    beyond metric ids and the four allowed functions."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ClaimExprError(f"claim expression {expr!r} does not parse: {e.msg}") from None

    def walk(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(n.value, (int, float)):
                raise ClaimExprError(f"claim expression {expr!r}: only numeric constants are allowed")
            return Decimal(str(n.value))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = walk(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            a, b = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                if b == 0:
                    raise ClaimExprError(f"claim expression {expr!r} divides by zero")
                return a / b
            raise ClaimExprError(f"claim expression {expr!r}: operator {type(n.op).__name__} is not allowed")
        if isinstance(n, ast.Call):
            name = n.func.id if isinstance(n.func, ast.Name) else None
            if name not in _FUNCS or n.keywords:
                raise ClaimExprError(f"claim expression {expr!r}: only {sorted(_FUNCS)} may be called")
            return _FUNCS[name](*(walk(a) for a in n.args))
        dotted = _dotted(n)
        if dotted is not None:
            if "." not in dotted:
                raise ClaimExprError(f"claim expression {expr!r}: {dotted!r} is not a metric id "
                                     f"(ids are <period>.<measure>)")
            return lookup(dotted)
        raise ClaimExprError(f"claim expression {expr!r}: {type(n).__name__} is not allowed")

    value = walk(tree)
    if not isinstance(value, Decimal):
        return Decimal(str(value))
    return value


def _assertion(spec: str) -> Callable[[Decimal], bool]:
    m = _ASSERT.match(spec or "")
    if not m:
        raise NarrativeError(f"unknown claim assertion {spec!r}; use positive, negative, nonzero, "
                             f"between(a, b), at_least(x) or at_most(x)")
    kind, args = m.group(1), [a.strip() for a in (m.group(2) or "").split(",") if a.strip()]
    try:
        nums = [Decimal(a) for a in args]
    except InvalidOperation:
        raise NarrativeError(f"claim assertion {spec!r} has a non-numeric bound") from None
    if kind == "positive":
        return lambda v: v > 0
    if kind == "negative":
        return lambda v: v < 0
    if kind == "nonzero":
        return lambda v: v != 0
    if kind == "between" and len(nums) == 2:
        lo, hi = nums
        return lambda v: lo <= v <= hi
    if kind == "at_least" and len(nums) == 1:
        return lambda v: v >= nums[0]
    if kind == "at_most" and len(nums) == 1:
        return lambda v: v <= nums[0]
    raise NarrativeError(f"claim assertion {spec!r} has the wrong number of bounds")


def _renderer(spec: Any) -> Callable[[Decimal], str]:
    """`render` is a format string applied to the value, or a mapping
    {positive, negative, zero} of format strings chosen by the value's sign
    and applied to its absolute value - for prose like "$X under plan" /
    "$X over plan" that must not be typed twice."""
    if isinstance(spec, str):
        return lambda v: spec.format(v)
    if isinstance(spec, Mapping):
        for k in ("positive", "negative"):
            if k not in spec:
                raise NarrativeError(f"a signed claim render needs both 'positive' and 'negative'; got {sorted(spec)}")

        def by_sign(v: Decimal) -> str:
            if v > 0:
                return str(spec["positive"]).format(abs(v))
            if v < 0:
                return str(spec["negative"]).format(abs(v))
            return str(spec.get("zero", spec["positive"])).format(abs(v))
        return by_sign
    raise NarrativeError("a claim needs a 'render' format string (or a {positive, negative} mapping)")


@dataclass(frozen=True)
class ClaimSpec:
    claim_id: str
    expr: str
    assertion: str
    render: Any
    note: str | None = None

    def register(self, registry: MetricRegistry) -> None:
        check = _assertion(self.assertion)
        fmt = _renderer(self.render)

        def lookup(metric_id: str) -> Decimal:
            return _raw(registry.get(metric_id))

        registry.register_claim(self.claim_id, lambda: evaluate(self.expr, lookup),
                                assert_fn=check, render=fmt)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def markdown_to_html(text: str) -> str:
    import markdown  # imported here so the data layer never depends on it
    return markdown.markdown(text, extensions=["tables"], output_format="html5")


# ---------------------------------------------------------------------------
# The content file
# ---------------------------------------------------------------------------

@dataclass
class Narrative:
    period: str
    dashboard: str
    path: Path
    meta: dict
    body: str
    claims: dict[str, ClaimSpec] = field(default_factory=dict)
    not_carried_forward: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, path: Path, *, period: str, dashboard: str) -> "Narrative":
        m = _FRONT.match(text)
        if not m:
            raise NarrativeError(f"{path}: no YAML front-matter (the file must start with '---')")
        meta = yaml.safe_load(m.group(1)) or {}
        if not isinstance(meta, dict):
            raise NarrativeError(f"{path}: front-matter must be a mapping")
        got_period, got_dash = str(meta.get("period", "")), str(meta.get("dashboard", ""))
        if got_period != period or got_dash != dashboard:
            raise NarrativeError(f"{path}: front-matter says period={got_period!r} dashboard={got_dash!r}; "
                                 f"this file is being built as {period!r}/{dashboard!r}")
        claims: dict[str, ClaimSpec] = {}
        for cid, spec in (meta.get("claims") or {}).items():
            if not isinstance(spec, Mapping) or "expr" not in spec or "assert" not in spec or "render" not in spec:
                raise NarrativeError(f"{path}: claim {cid!r} needs expr, assert and render")
            claims[str(cid)] = ClaimSpec(str(cid), str(spec["expr"]), str(spec["assert"]), spec["render"],
                                         spec.get("note"))
        ncf = meta.get("not_carried_forward") or []
        if not isinstance(ncf, list) or not all(isinstance(s, str) for s in ncf):
            raise NarrativeError(f"{path}: not_carried_forward must be a list of strings")
        return cls(period, dashboard, path, meta, text[m.end():], claims, list(ncf))

    def register_claims(self, registry: MetricRegistry) -> None:
        for spec in self.claims.values():
            spec.register(registry)

    def render(self, registry: MetricRegistry) -> "RenderedNarrative":
        env = make_env(registry)
        env.globals["d"] = registry.delta_between
        resolved = env.from_string(self.body).render()
        html = markdown_to_html(resolved)
        html = _H1.sub("", html, count=1)          # the page shell carries the title
        parts = _H2_SPLIT.split(html)
        intro = parts[0].strip()
        sections: dict[str, tuple[str, str]] = {}
        for i in range(1, len(parts), 2):
            heading, body = parts[i], parts[i + 1].strip()
            slug = slugify(heading)
            if slug in sections:
                raise NarrativeError(f"{self.path}: two sections slug to {slug!r}; rename one heading")
            sections[slug] = (_TAGS.sub("", heading).strip(), body)
        return RenderedNarrative(self.period, self.dashboard, str(self.path), intro, sections,
                                 list(self.not_carried_forward))


@dataclass
class RenderedNarrative:
    """What the template sees. Every accessor records what was placed."""

    period: str
    dashboard: str
    source: str
    intro_html: str
    sections: dict[str, tuple[str, str]]          # slug -> (heading text, html)
    not_carried: list[str]
    pending_reason: str | None = None
    _placed: set[str] = field(default_factory=set)

    @classmethod
    def pending(cls, period: str, dashboard: str, reason: str) -> "RenderedNarrative":
        return cls(period, dashboard, "", "", {}, [], pending_reason=reason)

    @classmethod
    def empty(cls, period: str, dashboard: str) -> "RenderedNarrative":
        """No prose at all and nothing pending - for tests and for pages that carry none."""
        return cls(period, dashboard, "", "", {}, [])

    @property
    def is_pending(self) -> bool:
        return self.pending_reason is not None

    def status(self) -> Markup:
        """A callout when the story has not been written; empty otherwise."""
        if not self.is_pending:
            return Markup("")
        return Markup(f'<div class="callout callout-pending" role="status" data-pending="narrative">'
                      f'<strong>Narrative pending.</strong> {escape(self.pending_reason)}</div>')

    def has(self, slug: str) -> bool:
        return slug in self.sections

    def heading(self, slug: str) -> str:
        return self.sections[slug][0] if slug in self.sections else ""

    def section(self, slug: str) -> Markup:
        """The prose for one section, or empty if this month's story has none."""
        self._placed.add(slug)
        if slug not in self.sections:
            return Markup("")
        return Markup(f'<div class="narrative" data-narrative="{escape(slug)}">{self.sections[slug][1]}</div>')

    def intro(self) -> Markup:
        self._placed.add("__intro__")
        if not self.intro_html:
            return Markup("")
        return Markup(f'<div class="narrative" data-narrative="intro">{self.intro_html}</div>')

    def not_carried_forward(self) -> Markup:
        """Retired v1 findings, quoted with the reason. Marked data-retired so
        the orphaned-number check knows a quotation is not a statement."""
        self._placed.add("__ncf__")
        if not self.not_carried:
            return Markup("")
        items = "".join(f"<li>{escape(s)}</li>" for s in self.not_carried)
        return Markup(f'<div class="narrative narrative-retired" data-narrative="not-carried-forward" data-retired>'
                      f'<ol>{items}</ol></div>')

    def unplaced(self) -> list[str]:
        """Sections written this month that no template slot asked for."""
        out = [s for s in self.sections if s not in self._placed]
        if self.intro_html and "__intro__" not in self._placed:
            out.append("(intro)")
        if self.not_carried and "__ncf__" not in self._placed:
            out.append("(not_carried_forward)")
        return out


def load_narrative(period: str, dashboard: str, root: Path = CONTENT) -> Narrative | None:
    """The content file for one page and month, or None if it is not written yet."""
    path = Path(root) / period / f"{dashboard}.md"
    if not path.exists():
        return None
    return Narrative.parse(path.read_text(encoding="utf-8"), path, period=period, dashboard=dashboard)
