"""The gate: every check, one report, exit 1 on any failure.

The build calls run_gate() on the rendered dist directory and stops if the
report is not ok. There is no "publish anyway" flag. Every check in this
package maps to a bug that was found in a v1 dashboard AFTER it had been
presented, and the cost of each was a conversation that started "actually,
that number is wrong". A red gate is cheaper than that conversation.

Usage:

    python -m src.validate.gate dist/ --period 2026-08 [--registry reg.json]

The report groups findings by check and file so a human can read it, and
to_markdown() is what lands in the build log.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import code, language, narrative, numbers, structural
from .dom import Node, parse
from .findings import FAIL, WARN, Finding

__all__ = ["run_gate", "GateReport", "Finding", "main"]

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class GateReport:
    dist_dir: str
    reporting_period: str
    failures: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def findings(self) -> list[Finding]:
        return self.failures + self.warnings

    def by_check(self) -> dict[str, int]:
        return dict(Counter(f.check for f in self.failures))

    def to_markdown(self) -> str:
        out = [f"# Validation gate — {self.dist_dir} (period {self.reporting_period})", ""]
        out.append(f"**{'PASS' if self.ok else 'FAIL'}** — {len(self.failures)} failure(s), "
                   f"{len(self.warnings)} warning(s) across {len(self.files)} page(s).")
        if self.stats:
            out.append("")
            out.append("Stats: " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items())))
        for title, items in (("Failures", self.failures), ("Warnings", self.warnings)):
            out += ["", f"## {title} ({len(items)})", ""]
            if not items:
                out.append("_none_")
                continue
            out += ["| Check | File | Message | Evidence |", "|---|---|---|---|"]
            for f in items:
                out.append(f"| `{f.check}` | {f.file} | {_md(f.message)} | {_md(f.evidence)} |")
        return "\n".join(out) + "\n"

    def console(self, max_per_check: int = 12) -> str:
        lines = ["=" * 78,
                 f"VALIDATION GATE {'PASSED' if self.ok else 'FAILED'}: {len(self.failures)} failure(s), "
                 f"{len(self.warnings)} warning(s), {len(self.files)} page(s)", "=" * 78]
        for sev, items in ((FAIL, self.failures), (WARN, self.warnings)):
            groups: dict[str, list[Finding]] = {}
            for f in items:
                groups.setdefault(f.check, []).append(f)
            for check, fs in sorted(groups.items()):
                lines.append(f"\n{sev.upper()} {check} ({len(fs)})")
                for f in fs[:max_per_check]:
                    ev = f"\n        [{_trim(f.evidence)}]" if f.evidence else ""
                    lines.append(f"  - {f.file}: {f.message}{ev}")
                if len(fs) > max_per_check:
                    lines.append(f"  … {len(fs) - max_per_check} more")
        lines.append("")
        return "\n".join(lines)


def _md(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")[:300]


def _trim(s: str, n: int = 200) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _html_files(dist_dir: Path) -> list[Path]:
    if dist_dir.is_file():
        return [dist_dir]
    return sorted(p for p in dist_dir.rglob("*.html") if p.is_file())


def page_checks(doc: Node, html: str, file: str, reporting_period: str) -> list[Finding]:
    """Every per-page check. Cross-page checks live in run_gate."""
    out: list[Finding] = []
    out += numbers.check_breakdown_tables(doc, file)
    out += numbers.check_component_lists(doc, file)
    out += numbers.check_no_null_metrics(doc, file)
    out += numbers.check_tables_against_arrays(doc, file)
    out += language.check_forbidden_terms(doc, file)
    out += language.check_currency_period(doc, file)
    out += language.check_delta_direction(doc, file)
    out += language.check_percent_point_deltas(doc, file)
    out += narrative.check_orphaned_numbers(doc, file)
    out += narrative.check_stale_months(doc, file, reporting_period)
    out += structural.check_tag_balance(html, file)
    out += structural.check_canvas_bindings(doc, file)
    out += structural.check_month_pills(doc, file)
    out += structural.check_chart_clipping(doc, file)
    out += structural.check_undefined_classes(doc, file)
    out += structural.check_external_dependencies(doc, file)
    return out


def run_gate(dist_dir: Path | str, reporting_period: str,
             metric_registry: dict | None = None, *,
             src_root: Path | None = None, queries_dir: Path | None = None,
             check_code: bool = True, check_queries: bool = True) -> GateReport:
    """Run every check over dist_dir/**/*.html plus the source scans.

    `reporting_period` is 'YYYY-MM'. `metric_registry` is
    {metric_id: [selectors or data-metric values]} and is optional; the
    heuristic data-metric pass runs regardless.
    """
    dist = Path(dist_dir)
    report = GateReport(str(dist), reporting_period)
    if not dist.exists():
        report.failures.append(Finding("gate", str(dist), "dist directory does not exist"))
        return report

    docs: list[tuple[str, Node]] = []
    findings: list[Finding] = []
    helpers: list[structural.DeltaHelper] = []
    for path in _html_files(dist):
        rel = str(path.relative_to(dist)) if dist.is_dir() else path.name
        html = path.read_text(encoding="utf-8", errors="replace")
        doc = parse(html)
        docs.append((rel, doc))
        report.files.append(rel)
        findings += page_checks(doc, html, rel, reporting_period)
        helpers += structural.delta_helper_definitions(html, rel)

    findings += numbers.check_metric_consistency(docs, metric_registry)
    findings += structural.check_delta_helpers(helpers)
    if check_queries:
        findings += numbers.check_queries(queries_dir or numbers.QUERIES_DIR)
    if check_code:
        findings += code.check_code(src_root or code.SRC_ROOT)

    report.stats = {"pages": len(docs), "delta_helpers": len(helpers)}
    for f in findings:
        (report.failures if f.is_failure else report.warnings).append(f)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.validate.gate",
                                 description="Validate rendered dashboards. Exit 1 on any failure.")
    ap.add_argument("dist", help="directory of rendered HTML (or a single file)")
    ap.add_argument("--period", default="auto",
                    help="reporting period, YYYY-MM; 'auto' (the default) is the month before today, the same "
                         "rule the build uses, so Cloudflare's bare 'python3 -m src.validate.gate dist' works")
    ap.add_argument("--registry", help="JSON file: {metric_id: [selectors]}")
    ap.add_argument("--markdown", help="write the report as Markdown to this path")
    ap.add_argument("--no-code", action="store_true", help="skip the src/ AST scan")
    ap.add_argument("--no-queries", action="store_true", help="skip the SQL text assertions")
    ap.add_argument("--quiet", action="store_true")
    ns = ap.parse_args(argv)

    period = ns.period
    if period == "auto":
        from datetime import date as _date
        from ..periods import reporting_month
        period = reporting_month(_date.today())
    registry = json.loads(Path(ns.registry).read_text()) if ns.registry else None
    report = run_gate(ns.dist, period, registry,
                      check_code=not ns.no_code, check_queries=not ns.no_queries)
    if ns.markdown:
        Path(ns.markdown).write_text(report.to_markdown())
    if not ns.quiet:
        print(report.console())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
