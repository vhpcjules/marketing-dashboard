"""AST scan of src/: one delta function, and nobody re-deriving it inline.

units.py says "There is exactly ONE delta function in the codebase. The
validation layer asserts this by AST scan." This is that scan.

Three things are looked for, in every src/**/*.py:

  1. Functions named `delta`. Exactly one is allowed, and it must be the one
     in src/units.py. A method named `delta` on a class is counted only if it
     takes two operands - `DriftFinding.delta` in freeze.py is a property
     returning a signed absolute difference (live - frozen) for the
     restatement report; it has one parameter, computes no ratio, and is not
     a relative change. The ratio scan below still applies to it.

  2. The relative-change expression: `(a - b) / b`, with or without `* 100`,
     where the subtrahend and the divisor are the same expression. Anywhere
     outside units.delta this is a second delta with a different name.

  3. A subtraction interpolated into an f-string that also carries a '%'
     literal - `f"{a - b}%"` - directly or via a local name bound to the
     subtraction. That is the exact shape of the v1 bug: a point difference
     wearing a percent sign.

Why AST and not grep: the text "(a - b) / b" has a hundred harmless
spellings and one meaning. The tree has the meaning.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .findings import Finding

__all__ = ["scan_source", "CodeScan", "check_code", "SRC_ROOT"]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
CANONICAL = "units.py"   # relative to src/


@dataclass(frozen=True)
class DeltaDef:
    file: str
    line: int
    name: str
    qualname: str


@dataclass
class CodeScan:
    delta_functions: list[DeltaDef]
    relative_change_sites: list[DeltaDef]     # functions containing (a-b)/b outside units.delta
    percent_format_sites: list[DeltaDef]      # functions formatting a subtraction with '%'
    files_scanned: int


def _same(a: ast.AST, b: ast.AST) -> bool:
    return ast.dump(a) == ast.dump(b)


def _is_relative_change(node: ast.AST) -> bool:
    """BinOp tree for (a - b) / b, optionally wrapped in `* 100` / `* Decimal(100)`."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        # (x) * 100  or  100 * (x)
        for side in (node.left, node.right):
            if _is_relative_change(side):
                return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        den = node.right
        # `100 * (a - b) / b` parses as `(100 * (a - b)) / b`: look through
        # multiplications in the numerator for the subtraction.
        for sub in _subtractions_through_mult(node.left):
            if _same(sub.right, den):
                return True
            # abs(b) as the divisor
            if (isinstance(den, ast.Call) and isinstance(den.func, ast.Name) and den.func.id == "abs"
                    and den.args and _same(sub.right, den.args[0])):
                return True
    return False


def _subtractions_through_mult(node: ast.AST) -> list[ast.BinOp]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        return [node]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _subtractions_through_mult(node.left) + _subtractions_through_mult(node.right)
    return []


def _percent_formats_subtraction(fn: ast.AST) -> bool:
    sub_names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Sub):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    sub_names.add(t.id)
    for node in ast.walk(fn):
        if not isinstance(node, ast.JoinedStr):
            continue
        has_pct = any(isinstance(v, ast.Constant) and isinstance(v.value, str) and "%" in v.value
                      for v in node.values)
        if not has_pct:
            continue
        for v in node.values:
            if not isinstance(v, ast.FormattedValue):
                continue
            inner = v.value
            if isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Sub):
                return True
            if isinstance(inner, ast.Name) and inner.id in sub_names:
                return True
    return False


def _operand_count(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = fn.args
    names = [a.arg for a in args.posonlyargs + args.args]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return len(names) + len(args.kwonlyargs)


def scan_source(src_root: Path = SRC_ROOT) -> CodeScan:
    deltas: list[DeltaDef] = []
    rel_sites: list[DeltaDef] = []
    pct_sites: list[DeltaDef] = []
    files = sorted(p for p in src_root.rglob("*.py") if "__pycache__" not in p.parts)
    for path in files:
        rel = str(path.relative_to(src_root.parent))
        tree = ast.parse(path.read_text(), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner = parents.get(node)
            qual = f"{owner.name}.{node.name}" if isinstance(owner, ast.ClassDef) else node.name
            d = DeltaDef(rel, node.lineno, node.name, qual)
            is_canonical = path.relative_to(src_root) == Path(CANONICAL) and node.name == "delta" and owner is tree
            if node.name == "delta" and (owner is tree or _operand_count(node) >= 2):
                deltas.append(d)
            if not is_canonical:
                if any(_is_relative_change(n) for n in ast.walk(node)):
                    rel_sites.append(d)
                if _percent_formats_subtraction(node):
                    pct_sites.append(d)
    return CodeScan(deltas, rel_sites, pct_sites, len(files))


def check_code(src_root: Path = SRC_ROOT) -> list[Finding]:
    scan = scan_source(src_root)
    out = []
    canonical_rel = str((src_root / CANONICAL).relative_to(src_root.parent))
    if len(scan.delta_functions) != 1:
        out.append(Finding(
            "code.single_delta", "src/",
            f"expected exactly one function named delta (in {canonical_rel}); found {len(scan.delta_functions)}",
            evidence=", ".join(f"{d.file}:{d.line} {d.qualname}" for d in scan.delta_functions) or "none",
        ))
    elif scan.delta_functions[0].file != canonical_rel:
        d = scan.delta_functions[0]
        out.append(Finding("code.single_delta", d.file,
                           f"the one delta function lives in {d.file}, not {canonical_rel}",
                           evidence=f"{d.file}:{d.line}"))
    for d in scan.relative_change_sites:
        out.append(Finding("code.inline_delta", d.file,
                           f"{d.qualname}() computes (a - b) / b inline; call src.units.delta instead",
                           evidence=f"{d.file}:{d.line}"))
    for d in scan.percent_format_sites:
        out.append(Finding("code.point_difference_formatted", d.file,
                           f"{d.qualname}() formats a subtraction next to a '%' sign - a point difference "
                           f"wearing a percent sign; use delta() or state the range",
                           evidence=f"{d.file}:{d.line}"))
    return out
