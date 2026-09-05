"""Markup and script structure: the page must hold together.

  tag_balance        opened and closed counts agree for the tags that matter.
                     A tolerant parser repairs this silently, which is why
                     the count is taken on a raw pass, not on the tree.
  canvas_binding     every <canvas id> is drawn on by some script, and every
                     chart's getElementById resolves to a canvas. A canvas
                     nobody draws is an empty box in a leadership deck; a
                     lookup that resolves to nothing is a runtime exception
                     that stops every chart after it.
  dead_lookup        every literal getElementById('x') resolves to an id.
  month_pill         every .month-pill carries data-month (the picker keys
                     its data on it; v1's selectMonth reads p.dataset.month).
  delta_helpers      count of client-side delta functions. src.units.delta is
                     the only delta; v1 had three (ldDeltaText, smDelta,
                     fmtDelta), each computing its own variant. More than one
                     in a dist fails the build.
  chart_clipping     a Chart.js scale with an explicit `max` below the data it
                     plots. Social_Media_Performance put LI_ENGAGEMENT_RATE
                     (14.31, 21.08) on an axis with max: 10 - the two best
                     months were drawn off the top of the chart.
  undefined_class    a class emitted by script that no stylesheet defines.
                     Leadership's CSS styled .delta.up/.down/.neutral; its JS
                     emitted 'delta flat'. Warning: it is invisible, not wrong.
  external_dependency a <script src> or <link href> to another host. Chart.js
                     is vendored; a CDN outage is a blank dashboard. Warning
                     for the corpus; the build's asset step vendors it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .dom import Node, tag_counts
from .findings import WARN, Finding
from .js import chart_blocks, external_scripts, inline_scripts, numeric_arrays, style_text
from .numeric import fmt

__all__ = [
    "BALANCED_TAGS", "check_tag_balance", "check_canvas_bindings", "check_month_pills",
    "delta_helper_definitions", "check_delta_helpers", "DeltaHelper",
    "check_chart_clipping", "check_undefined_classes", "check_external_dependencies",
]

BALANCED_TAGS = frozenset({"div", "table", "tr", "td", "th", "section", "span", "p",
                           "ul", "li", "a", "script"})


def check_tag_balance(html: str, file: str) -> list[Finding]:
    out = []
    for tag, (opened, closed) in tag_counts(html, BALANCED_TAGS).items():
        if opened != closed:
            out.append(Finding("structural.tag_balance", file,
                               f"<{tag}> opened {opened} times but closed {closed} times"))
    return out


# ---------------------------------------------------------------------------
# Canvas <-> chart binding
# ---------------------------------------------------------------------------

_GEBI = re.compile(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)")
_QS_ID = re.compile(r"querySelector\(\s*['\"]#([^'\"\s.\[]+)['\"]\s*\)")
_VAR_GEBI = re.compile(r"(?:const|let|var)\s+([\w$]+)\s*=\s*document\.getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)")
_NEW_CHART_VAR = re.compile(r"new\s+Chart\s*\(\s*([\w$]+)\s*[,)]")


def check_canvas_bindings(doc: Node, file: str) -> list[Finding]:
    out = []
    scripts = "\n".join(t for _, t in inline_scripts(doc))
    ids = {n.get("id") for n in doc.elements() if n.get("id")}
    canvases = {c.get("id"): c for c in doc.find_all("canvas") if c.get("id")}
    referenced = set(_GEBI.findall(scripts)) | set(_QS_ID.findall(scripts))
    referenced |= {n.get("data-chart") for n in doc.find_all(attr="data-chart") if n.get("data-chart")}

    for cid, c in canvases.items():
        if cid not in referenced:
            out.append(Finding("structural.canvas_binding", file,
                               f"<canvas id={cid!r}> is never referenced by a script or a data-chart binding",
                               evidence=f"line {c.line}"))

    var_to_id = dict(_VAR_GEBI.findall(scripts))
    for block in chart_blocks(scripts):
        target = block.canvas_ref
        if target is None:
            m = _NEW_CHART_VAR.match(block.source)
            if m and m.group(1) in var_to_id:
                target = var_to_id[m.group(1)]
        if target is None:
            continue  # indirect; cannot be resolved statically
        if target not in canvases:
            out.append(Finding("structural.canvas_binding", file,
                               f"new Chart(...) draws on getElementById({target!r}) which is not a <canvas> id in this page",
                               evidence=block.source[:120]))

    for ref in sorted(set(_GEBI.findall(scripts))):
        if ref not in ids:
            out.append(Finding("structural.dead_lookup", file,
                               f"getElementById({ref!r}) has no element with that id; the script will throw at runtime"))
    return out


def check_month_pills(doc: Node, file: str) -> list[Finding]:
    out = []
    for n in doc.find_all(cls="month-pill"):
        if not n.get("data-month"):
            out.append(Finding("structural.month_pill", file,
                               "element with class month-pill has no data-month",
                               evidence=f"<{n.tag}> {n.rendered_text()!r} line {n.line}"))
    return out


# ---------------------------------------------------------------------------
# Client-side delta helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeltaHelper:
    name: str
    file: str
    line: int
    kind: str      # function | arrow


_FN_DEF = re.compile(r"function\s+(\w*[Dd]elta\w*)\s*\(")
_ARROW_DEF = re.compile(r"(?:const|let|var)\s+(\w*[Dd]elta\w*)\s*=\s*(?:\([^)]*\)|\w+)\s*=>")
_ANON_ARROW_NEAR_PREV = re.compile(r"(?:\([^)]*\)|\w+)\s*=>\s*(?:\{[^}]{0,200}?|[^;{}]{0,120}?)\bcurr?\w*\s*-\s*prev")
# A binary minus between two operands - the thing a delta computes. Excludes
# the unicode dashes v1 used inside strings ('— vs').
_SUBTRACTION = re.compile(r"[\w)\]]\s*-\s*[\w(]")


def _body_after(text: str, idx: int) -> str:
    """The brace-delimited body starting at the first '{' after idx, or the
    rest of the statement for a brace-less arrow."""
    brace = text.find("{", idx)
    semi = text.find(";", idx)
    if brace == -1 or (semi != -1 and semi < brace):
        return text[idx: semi if semi != -1 else len(text)]
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    return text[brace:]


def delta_helper_definitions(html_or_script: str, file: str, *, is_script: bool = False) -> list[DeltaHelper]:
    """Functions whose NAME says delta and whose BODY subtracts.

    The name alone is not enough: v1's ldDeltaClass compares curr > prev and
    picks a colour, which is direction_class(), not delta(). Requiring a
    subtraction is what makes the corpus count come out at three - the three
    functions that each compute their own change figure.
    """
    from .dom import parse
    if is_script:
        texts = [(html_or_script, 0)]
    else:
        doc = parse(html_or_script)
        texts = [(t, n.line) for n, t in inline_scripts(doc)]
    out = []
    for text, base_line in texts:
        for pat, kind in ((_FN_DEF, "function"), (_ARROW_DEF, "arrow")):
            for m in pat.finditer(text):
                body = _body_after(text, m.end())
                if _SUBTRACTION.search(body):
                    out.append(DeltaHelper(m.group(1), file, base_line + text.count("\n", 0, m.start()), kind))
        for m in _ANON_ARROW_NEAR_PREV.finditer(text):
            out.append(DeltaHelper("<anonymous arrow>", file, base_line + text.count("\n", 0, m.start()), "arrow"))
    # De-duplicate by (name, line).
    seen, uniq = set(), []
    for h in out:
        if (h.name, h.line) not in seen:
            seen.add((h.name, h.line))
            uniq.append(h)
    return uniq


def check_delta_helpers(helpers: list[DeltaHelper]) -> list[Finding]:
    """Gate-level: the count is across the whole dist, not per page."""
    if len(helpers) > 1:
        return [Finding(
            "structural.delta_helpers",
            ", ".join(sorted({h.file for h in helpers})),
            f"{len(helpers)} client-side delta functions defined; src.units.delta is the only delta",
            evidence=", ".join(f"{h.name} ({h.file}:{h.line})" for h in helpers),
        )]
    if len(helpers) == 1:
        h = helpers[0]
        return [Finding("structural.delta_helpers", h.file,
                        f"client-side delta helper {h.name!r} duplicates src.units.delta; compute deltas in Python",
                        evidence=f"{h.file}:{h.line}", severity=WARN)]
    return []


# ---------------------------------------------------------------------------
# Chart.js axis clipping
# ---------------------------------------------------------------------------

_SCALE_MAX = re.compile(r"\b([\w$]+)\s*:\s*\{[^{}]*?\bmax\s*:\s*(\d+(?:\.\d+)?)")
_DATASET = re.compile(r"\{[^{}]*?\bdata\s*:\s*([\w$]+|\[[^\]]*\])[^{}]*\}", re.S)
_YAXIS = re.compile(r"\byAxisID\s*:\s*['\"]([\w$]+)['\"]")


def check_chart_clipping(doc: Node, file: str) -> list[Finding]:
    out = []
    script = "\n".join(t for _, t in inline_scripts(doc))
    if "new Chart" not in script:
        return out
    arrays = numeric_arrays(script)
    for block in chart_blocks(script):
        scales_idx = block.source.find("scales")
        if scales_idx == -1:
            continue
        maxes = {axis: Decimal(v) for axis, v in _SCALE_MAX.findall(block.source[scales_idx:])}
        if not maxes:
            continue
        for ds in _DATASET.finditer(block.source[:scales_idx] if scales_idx else block.source):
            src = ds.group(0)
            ref = ds.group(1)
            axis_m = _YAXIS.search(src)
            axis = axis_m.group(1) if axis_m else "y"
            if axis not in maxes:
                continue
            if ref.startswith("["):
                vals = numeric_arrays(f"const _x = {ref}").get("_x")
            else:
                vals = arrays.get(ref)
            if not vals:
                continue
            present = [v for v in vals if v is not None]
            if not present:
                continue
            peak = max(present)
            if peak > maxes[axis]:
                label_m = re.search(r"\blabel\s*:\s*['\"]([^'\"]*)['\"]", src)
                out.append(Finding(
                    "structural.chart_clipping", file,
                    f"chart on {block.canvas_ref!r}: axis {axis!r} has max: {fmt(maxes[axis])} but dataset "
                    f"{label_m.group(1) if label_m else ref!r} peaks at {fmt(peak)} - the peak is drawn off the chart",
                    evidence=f"{ref} = [{', '.join('null' if v is None else fmt(v) for v in vals)}]",
                ))
    return out


# ---------------------------------------------------------------------------
# Classes the scripts emit that the CSS never defines
# ---------------------------------------------------------------------------

_CSS_CLASS = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
_JS_CLASS_STRING = re.compile(r"""['"`]((?:[\w-]+\s+)+[\w-]+)['"`]""")
_CLASS_LIST_CALL = re.compile(r"classList\.(?:add|toggle|replace)\(\s*['\"]([\w-]+)['\"]")
_TEMPLATE_CLASS = re.compile(r"""class=\\?['"]([^'"$`<>]+)\\?['"]""")


def check_undefined_classes(doc: Node, file: str) -> list[Finding]:
    """Warn when a class emitted from script has no CSS rule.

    Only script-emitted class lists are checked, and only when at least one
    token in the list IS styled - that is the signature of a family with a
    missing member ('delta flat' next to .delta.up/.delta.down), as opposed
    to a hook class that is never meant to be styled.
    """
    css = style_text(doc)
    # External stylesheets cannot be read; only inline <style> counts.
    defined = set(_CSS_CLASS.findall(css))
    if not defined:
        return []
    scripts = "\n".join(t for _, t in inline_scripts(doc))
    emitted: dict[str, set[str]] = {}
    for m in _JS_CLASS_STRING.finditer(scripts):
        tokens = m.group(1).split()
        # Class lists are lowercase kebab-case and appear near the word
        # "class" (className, classList, a *Class() helper). "Last month" is
        # prose that happens to contain a styled token, and is skipped.
        if not all(re.fullmatch(r"[a-z][a-z0-9-]*", t) for t in tokens):
            continue
        if not re.search(r"class", scripts[max(0, m.start() - 400):m.start()], re.I):
            continue
        if any(t in defined for t in tokens):
            for t in tokens:
                emitted.setdefault(t, set()).update(tokens)
    for m in _TEMPLATE_CLASS.finditer(scripts):
        tokens = m.group(1).split()
        if any(t in defined for t in tokens):
            for t in tokens:
                emitted.setdefault(t, set()).update(tokens)
    for m in _CLASS_LIST_CALL.finditer(scripts):
        emitted.setdefault(m.group(1), set()).add(m.group(1))
    out = []
    for cls, siblings in sorted(emitted.items()):
        if cls in defined or cls in ("active",):
            continue
        if not any(s in defined for s in siblings if s != cls):
            continue
        out.append(Finding("structural.undefined_class", file,
                           f"script emits class {cls!r} but no stylesheet rule defines it "
                           f"(styled siblings: {sorted(s for s in siblings if s in defined)})",
                           severity=WARN))
    return out


def check_external_dependencies(doc: Node, file: str) -> list[Finding]:
    out = []
    for s in external_scripts(doc):
        src = s.get("src") or ""
        if re.match(r"^(?:https?:)?//", src):
            out.append(Finding("structural.external_dependency", file,
                               f"external dependency: script loaded from {src}; vendor it locally",
                               severity=WARN))
    for l in doc.find_all("link"):
        href = l.get("href") or ""
        if re.match(r"^(?:https?:)?//", href) and (l.get("rel") or "").lower() in ("stylesheet", "preload", "modulepreload"):
            out.append(Finding("structural.external_dependency", file,
                               f"external dependency: {l.get('rel')} loaded from {href}; vendor it locally",
                               severity=WARN))
    return out
