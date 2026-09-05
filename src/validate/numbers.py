"""Arithmetic the page must get right: totals, agreement, and the queries.

Each check here is a v1 bug with the serial number filed off:

  breakdown_table       Leadership "Are we spending wisely?": OVERALL M1
                        revenue $1,043,816 against subtotals of $214,723 +
                        $568,843. Nobody added the column. The gap is $260,250.
  component_list        Marketing Activity: "240 likes · 63 comments · 34 saves
                        · 29 shares" (366) under a tile that says 375.
  metric_consistency    The same figure rendered two ways on two pages, or
                        75 in a table against 74 in the array the chart plots.
  null_metric           "$None", "NaN%", an empty tile - a template variable
                        that did not resolve, shipped.
  queries               The NET revenue join with a load-bearing clause missing
                        added $11,703 of freight to "NET" revenue. These SQL
                        assertions duplicate tests/test_spend.py on purpose: the
                        gate must run standalone, in the build, with no pytest.

Every comparison is Decimal. Tolerances: $1 for currency, 0.5 for counts,
scaled for K/M-suffixed figures because those are rounded before summing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .dom import Node
from .findings import Finding
from .js import inline_scripts, numeric_arrays, string_arrays
from .months import parse_month_label
from .numeric import CURRENCY, PCT, DIGIT_GROUP, ParsedNumber, fmt, parse_number

__all__ = [
    "check_breakdown_tables", "check_component_lists", "check_metric_consistency",
    "check_no_null_metrics", "check_tables_against_arrays", "check_queries",
    "select", "QUERY_RULES",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
QUERIES_DIR = REPO_ROOT / "src" / "data" / "queries"

# Column headers that are not additive: averages, rates, per-unit figures.
# "Cost per Customer" summed across channels is meaningless, and v1's
# subtotal row correctly did not sum it - so neither do we.
NON_ADDITIVE_HEADER = re.compile(
    r"\b(per|avg|average|mean|median|rate|ratio|multiple|share|mix|cpm|cpc|cpa|cpl|roas)\b|%|/",
    re.I,
)
TOTAL_LABEL = re.compile(r"^\W*(grand\s+total|overall|total)\b", re.I)
SUBTOTAL_LABEL = re.compile(r"\bsub-?total\b", re.I)


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------

@dataclass
class _Row:
    node: Node
    cells: list[Node | None]      # colspan-expanded; None = spanned placeholder
    kind: str                     # header | body | subtotal | total | section

    @property
    def label(self) -> str:
        return self.cells[0].rendered_text() if self.cells and self.cells[0] is not None else ""


def _expand(cells: list[Node]) -> list[Node | None]:
    out: list[Node | None] = []
    for c in cells:
        out.append(c)
        try:
            span = int(c.get("colspan") or 1)
        except ValueError:
            span = 1
        out.extend([None] * max(0, span - 1))
    return out


def _direct_cells(tr: Node) -> list[Node]:
    return [c for c in tr.children if c.is_element and c.tag in ("td", "th")]


def _rows(table: Node) -> list[_Row]:
    rows = []
    for tr in table.find_all("tr"):
        # Nested tables belong to themselves.
        if tr.closest(lambda n: n.tag == "table") is not table:
            continue
        cells = _direct_cells(tr)
        if not cells:
            continue
        expanded = _expand(cells)
        if all(c.tag == "th" for c in cells):
            kind = "header"
        else:
            first = cells[0]
            label = first.rendered_text()
            span = int(first.get("colspan") or 1)
            if span > 1 and len(cells) == 1:
                kind = "section"
            elif SUBTOTAL_LABEL.search(label):
                kind = "subtotal"
            elif TOTAL_LABEL.match(label) or "total" in tr.classes() or "total" in first.classes():
                kind = "total"
            else:
                kind = "body"
        rows.append(_Row(tr, expanded, kind))
    return rows


def _cell_number(cell: Node | None) -> ParsedNumber | None:
    if cell is None:
        return None
    return parse_number(cell.rendered_text())


def _tolerance(values: list[ParsedNumber], total: ParsedNumber) -> Decimal:
    approx = total.approx or any(v.approx for v in values)
    if approx:
        # Each K-rounded figure can be off by up to 500; n inputs plus the total.
        unit = Decimal(500) if "K" in total.text.upper() or any("K" in v.text.upper() for v in values) else Decimal(500_000)
        return unit * (len(values) + 1)
    if total.kind == CURRENCY or any(v.kind == CURRENCY for v in values):
        return Decimal(1)
    return Decimal("0.5")


def _compare_group(file: str, table_idx: int, header: list[str], group: list[_Row],
                   target: _Row, what: str) -> list[Finding]:
    out = []
    ncols = max([len(target.cells)] + [len(r.cells) for r in group])
    for col in range(1, ncols):
        head = header[col] if col < len(header) else f"column {col + 1}"
        head_node_flag = False
        if NON_ADDITIVE_HEADER.search(head):
            continue
        total_cell = target.cells[col] if col < len(target.cells) else None
        if total_cell is not None and (total_cell.get("data-additive") == "false"):
            continue
        total = _cell_number(total_cell)
        if total is None or total.kind == PCT:
            continue
        values = []
        for r in group:
            cell = r.cells[col] if col < len(r.cells) else None
            if cell is not None and cell.get("data-additive") == "false":
                head_node_flag = True
                break
            n = _cell_number(cell)
            if n is None:
                continue          # "n/a", "—", "$0 direct": labels contribute nothing
            if n.kind == PCT:
                values = []
                break
            values.append(n)
        if head_node_flag or not values:
            continue
        s = sum((v.value for v in values), Decimal(0))
        tol = _tolerance(values, total)
        gap = total.value - s
        if abs(gap) > tol:
            out.append(Finding(
                "numbers.breakdown_table", file,
                f"table {table_idx} {what} row {target.label!r}, column {head!r}: "
                f"the {len(values)} rows above sum to {fmt(s)} but the row shows "
                f"{fmt(total.value)} (gap {fmt(gap)})",
                evidence=" | ".join(_cell_text(r.cells[col] if col < len(r.cells) else None) for r in group)
                         + f" => {total.text}",
            ))
    return out


def _cell_text(c: Node | None) -> str:
    return c.rendered_text() if c is not None else ""


def check_breakdown_tables(doc: Node, file: str) -> list[Finding]:
    """Every column of a table with a Total/Subtotal/OVERALL row must add up.

    Two shapes are handled:
      plain      body rows ... Total      -> Total == sum(body)
      sectioned  body ... Subtotal, body ... Subtotal, OVERALL
                 -> each Subtotal == sum(its body); OVERALL == sum(Subtotals)

    A total row also resets the group, so a table with several independent
    totals (one per section) is checked section by section.
    """
    out: list[Finding] = []
    for t_idx, table in enumerate(doc.find_all("table"), start=1):
        rows = _rows(table)
        if not any(r.kind in ("total", "subtotal") for r in rows):
            continue
        header: list[str] = []
        for r in rows:
            if r.kind == "header":
                header = [_cell_text(c) for c in r.cells]
                break
        group: list[_Row] = []
        subtotals: list[_Row] = []
        for r in rows:
            if r.kind in ("header", "section"):
                continue
            if r.kind == "body":
                group.append(r)
            elif r.kind == "subtotal":
                out += _compare_group(file, t_idx, header, group, r, "subtotal")
                subtotals.append(r)
                group = []
            elif r.kind == "total":
                if subtotals:
                    # Trailing body rows after the last subtotal count too.
                    out += _compare_group(file, t_idx, header, subtotals + group, r, "total-of-subtotals")
                else:
                    out += _compare_group(file, t_idx, header, group, r, "total")
                group, subtotals = [], []
    return out


# ---------------------------------------------------------------------------
# Component lists in narrative: "240 likes · 63 comments · 34 saves · 29 shares"
# ---------------------------------------------------------------------------

_COMPONENT_LIST = re.compile(
    r"(\d[\d,]*)\s+([A-Za-z][\w-]*)(?:\s*[·•]\s*(\d[\d,]*)\s+([A-Za-z][\w-]*)){2,}"
)
_COMPONENT_ITEM = re.compile(r"(\d[\d,]*)\s+([A-Za-z][\w-]*)")
TILE_CLASS = re.compile(r"\b(tile|kpi|stat|card|metric|callout)\b")


def _container(node: Node) -> Node:
    tile = node.closest(lambda n: n is not node and bool(TILE_CLASS.search(n.get("class") or "")))
    if tile is not None:
        return tile
    return node.parent.parent if node.parent is not None and node.parent.parent is not None else node


def check_component_lists(doc: Node, file: str) -> list[Finding]:
    """A dotted breakdown must add up to the total it sits under.

    The stated total is the tile's `.value` element when there is one, else
    the last number that appears in the container before the breakdown.
    """
    out: list[Finding] = []
    seen = set()
    for tn in doc.text_nodes():
        text = tn.text
        for m in _COMPONENT_LIST.finditer(text):
            key = (id(tn.parent), m.group(0))
            if key in seen:
                continue
            seen.add(key)
            items = _COMPONENT_ITEM.findall(m.group(0))
            total = sum(Decimal(n.replace(",", "")) for n, _ in items)
            container = _container(tn.parent)
            stated: Decimal | None = None
            values = container.find_all(cls="value")
            if values:
                p = parse_number(values[0].rendered_text())
                stated = p.value if p else None
            if stated is None:
                before = container.rendered_text().split(m.group(0))[0]
                nums = DIGIT_GROUP.findall(before)
                if nums:
                    stated = Decimal(nums[-1].replace(",", ""))
            if stated is None:
                continue
            if stated != total:
                out.append(Finding(
                    "numbers.component_list", file,
                    f"breakdown sums to {fmt(total)} but the stated total is {fmt(stated)}",
                    evidence=m.group(0),
                ))
    return out


# ---------------------------------------------------------------------------
# Same-metric consistency
# ---------------------------------------------------------------------------

_SELECTOR = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*)?(?P<rest>(?:#[\w-]+|\.[\w-]+|\[[\w-]+(?:=\"?[^\]\"]*\"?)?\])*)$"
)
# A bare word that is one of these is a tag selector; any other bare word is
# a data-metric value ("m1_2026_08"), so registries can be written either way.
_HTML_TAGS = frozenset({
    "a", "article", "aside", "b", "button", "canvas", "caption", "dd", "div", "dl", "dt", "em",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "i", "li",
    "main", "nav", "ol", "p", "section", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "time", "tr", "ul",
})
_PART = re.compile(r"#(?P<id>[\w-]+)|\.(?P<cls>[\w-]+)|\[(?P<attr>[\w-]+)(?:=\"?(?P<val>[^\]\"]*)\"?)?\]")


def select(doc: Node, selector: str) -> list[Node]:
    """A compound selector: tag, #id, .class, [attr], [attr=value]. No combinators.

    A string that is not a selector is taken as a data-metric value, so a
    registry can be written either way.
    """
    selector = selector.strip()
    m = _SELECTOR.match(selector)
    bare_word = m is not None and m.group("tag") and not m.group("rest")
    if not m or bare_word and m.group("tag").lower() not in _HTML_TAGS:
        return [n for n in doc.find_all(attr="data-metric") if n.get("data-metric") == selector]
    tag = m.group("tag")
    parts = _PART.findall(m.group("rest") or "")
    out = []
    for n in doc.elements(tag):
        ok = True
        for id_, cls, attr, val in parts:
            if id_ and n.get("id") != id_:
                ok = False
            elif cls and not n.has_class(cls):
                ok = False
            elif attr and (attr not in n.attrs or (val and n.attrs[attr] != val)):
                ok = False
            if not ok:
                break
        if ok:
            out.append(n)
    return out


def check_metric_consistency(docs: list[tuple[str, Node]],
                             registry: dict[str, list[str]] | None = None) -> list[Finding]:
    """Every occurrence of one metric must render identically, across pages.

    Registry-driven first ({metric_id: [selectors]}), then the heuristic
    pass: any two elements sharing a data-metric value must have the same
    text. Cross-file on purpose - "cross-dashboard agreement to the cent" is
    the requirement, and a metric id that means different things on two
    pages is a naming bug worth surfacing.
    """
    out: list[Finding] = []

    def _report(check: str, metric: str, occurrences: list[tuple[str, str]]) -> None:
        distinct = sorted({t for _, t in occurrences})
        if len(distinct) > 1:
            where = "; ".join(f"{f}: {t!r}" for f, t in occurrences)
            files = sorted({f for f, _ in occurrences})
            out.append(Finding(check, ", ".join(files),
                               f"metric {metric!r} renders {len(distinct)} different ways: "
                               + ", ".join(repr(d) for d in distinct),
                               evidence=where))

    for metric, selectors in (registry or {}).items():
        occ = []
        for file, doc in docs:
            for sel in selectors:
                for n in select(doc, sel):
                    occ.append((file, n.rendered_text()))
        _report("numbers.metric_registry", metric, occ)

    by_id: dict[str, list[tuple[str, str]]] = {}
    for file, doc in docs:
        for n in doc.find_all(attr="data-metric"):
            by_id.setdefault(n.get("data-metric") or "", []).append((file, n.rendered_text()))
    for metric, occ in sorted(by_id.items()):
        _report("numbers.metric_consistency", metric, occ)
    return out


# ---------------------------------------------------------------------------
# NULL / None / nan / undefined / blank
# ---------------------------------------------------------------------------

_NULLISH = re.compile(r"(?<![\w-])(null|none|nan|undefined|nat|inf)(?![\w-])", re.I)


def check_no_null_metrics(doc: Node, file: str) -> list[Finding]:
    out = []
    for n in doc.find_all(attr="data-metric"):
        text = n.rendered_text()
        if not text:
            out.append(Finding("numbers.null_metric", file,
                               f"data-metric {n.get('data-metric')!r} is blank", evidence=f"<{n.tag}> line {n.line}"))
            continue
        m = _NULLISH.search(text)
        if m:
            out.append(Finding("numbers.null_metric", file,
                               f"data-metric {n.get('data-metric')!r} renders an unresolved value {m.group(1)!r}",
                               evidence=text))
    return out


# ---------------------------------------------------------------------------
# Table cells vs the arrays the charts plot
# ---------------------------------------------------------------------------

def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t and t not in ("the", "of", "per", "no")}


def check_tables_against_arrays(doc: Node, file: str) -> list[Finding]:
    """A month-keyed table must agree with a same-named chart array.

    Social_Media_Performance: the LinkedIn table said Mar 2026 gained 75
    followers; LI_NEW_FOLLOWERS, which the chart plots, said 74. Same page,
    two numbers, and nobody could say which was right.

    Matching is by header tokens being a subset of the array name's tokens
    ("New followers" ⊆ LI_NEW_FOLLOWERS), rows are aligned by month label
    against a string array of month labels of the same length, and when a
    table's columns match arrays with different prefixes (LI_* vs META_*)
    the majority prefix wins and the rest are dropped as ambiguous. This is a
    heuristic and says so in the finding.
    """
    out: list[Finding] = []
    script = "\n".join(t for _, t in inline_scripts(doc))
    if not script:
        return out
    num_arrays = numeric_arrays(script)
    label_arrays = {
        name: [parse_month_label(s) for s in vals]
        for name, vals in string_arrays(script).items()
    }
    label_arrays = {n: v for n, v in label_arrays.items() if v and all(v)}
    if not num_arrays or not label_arrays:
        return out

    for t_idx, table in enumerate(doc.find_all("table"), start=1):
        rows = _rows(table)
        header = next(([_cell_text(c) for c in r.cells] for r in rows if r.kind == "header"), None)
        if not header:
            continue
        body = [r for r in rows if r.kind == "body"]
        row_months = [parse_month_label(r.label) for r in body]
        if sum(1 for m in row_months if m) < 2:
            continue
        # Two rows for one month (Facebook / Instagram) is a table keyed on
        # month AND platform; a flat array cannot be aligned to it.
        keyed = [m for m in row_months if m]
        if len(keyed) != len(set(keyed)):
            continue

        # Candidate arrays per column.
        candidates: dict[int, list[str]] = {}
        for col, head in enumerate(header[1:], start=1):
            ht = _tokens(head)
            if not ht:
                continue
            cands = [name for name in num_arrays if ht <= _tokens(name)]
            if cands:
                candidates[col] = cands
        if not candidates:
            continue
        prefixes: dict[str, int] = {}
        for cands in candidates.values():
            for name in cands:
                p = name.split("_")[0].lower()
                prefixes[p] = prefixes.get(p, 0) + 1
        best = max(prefixes.values())
        winners = [p for p, c in prefixes.items() if c == best]
        if len(winners) != 1 and len(prefixes) > 1:
            continue  # ambiguous: two array families equally plausible
        prefix = winners[0]

        for col, cands in candidates.items():
            names = [n for n in cands if n.split("_")[0].lower() == prefix]
            if len(names) != 1:
                continue
            name = names[0]
            arr = num_arrays[name]
            labels = [la for la in label_arrays.values() if len(la) == len(arr)]
            if len(labels) != 1:
                continue
            months = labels[0]
            for r, rm in zip(body, row_months):
                if rm is None:
                    continue
                idx = [i for i, lm in enumerate(months)
                       if lm[0] == rm[0] and (lm[1] is None or rm[1] is None or lm[1] == rm[1])]
                if len(idx) != 1:
                    continue
                arr_v = arr[idx[0]]
                cell = _cell_number(r.cells[col] if col < len(r.cells) else None)
                if arr_v is None or cell is None:
                    continue
                q = Decimal(1).scaleb(-cell.decimals)
                if arr_v.quantize(q) != cell.value:
                    out.append(Finding(
                        "numbers.table_vs_chart_array", file,
                        f"table {t_idx} row {r.label!r} column {header[col]!r} shows "
                        f"{cell.text} but chart array {name}[{idx[0]}] is {fmt(arr_v)} "
                        f"(heuristic match by name)",
                        evidence=f"{name} = [{', '.join('null' if v is None else fmt(v) for v in arr)}]",
                    ))
    return out


# ---------------------------------------------------------------------------
# Query text
# ---------------------------------------------------------------------------

def executable_sql(text: str) -> str:
    """The SQL with `--` comments removed. Assertions must hold on what runs,
    not on what the comments promise."""
    lines = []
    for line in text.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True)
class QueryRule:
    file: str
    pattern: str
    why: str
    must_match: bool = True


QUERY_RULES = [
    QueryRule("net_revenue_monthly.sql", r"i\.itemtype\s+IS\s+NOT\s+NULL",
              "freight/shipping items have NULL itemtype; without this $11,703 of shipping was 'NET' revenue in v1"),
    QueryRule("net_revenue_monthly.sql", r"'CustInvc'", "all four transaction types"),
    QueryRule("net_revenue_monthly.sql", r"'CashSale'", "all four transaction types"),
    QueryRule("net_revenue_monthly.sql", r"'CustCred'", "credit memos are returns; drop this and returns vanish"),
    QueryRule("net_revenue_monthly.sql", r"'CustRfnd'", "refunds are returns; drop this and returns vanish"),
    QueryRule("net_revenue_monthly.sql", r"SUM\(\s*-\s*tl\.foreignamount\s*\)", "the sign flip is required"),
    QueryRule("net_revenue_monthly.sql", r"\bCOALESCE\s*\(", "SUM() of an empty period is NULL, not 0"),
    QueryRule("net_revenue_monthly.sql", r"\bt\.subsidiary\b", "the subsidiary filter belongs in the SQL, not in prose"),
    QueryRule("marketing_spend_monthly.sql", r"NOT\s+LIKE\s+'96212%'", "96212.* is the NAF, the GarageExperts franchisee fund"),
    QueryRule("marketing_spend_monthly.sql", r"LIKE\s+'66212%'", "marketing spend include pattern"),
    QueryRule("marketing_spend_monthly.sql", r"LIKE\s+'66215%'", "marketing spend include pattern"),
    QueryRule("marketing_spend_monthly.sql", r"\bt\.subsidiary\b", "the subsidiary filter belongs in the SQL"),
    QueryRule("marketing_spend_monthly.sql", r"LIKE\s+'%", "an unanchored LIKE matches the NAF accounts too", must_match=False),
    QueryRule("net_revenue_monthly.sql", r"\bgross\b", "there is no gross figure anywhere", must_match=False),
]


def check_queries(queries_dir: Path = QUERIES_DIR) -> list[Finding]:
    out = []
    cache: dict[str, str | None] = {}
    for rule in QUERY_RULES:
        if rule.file not in cache:
            p = queries_dir / rule.file
            cache[rule.file] = executable_sql(p.read_text()) if p.exists() else None
        sql = cache[rule.file]
        rel = f"src/data/queries/{rule.file}"
        if sql is None:
            out.append(Finding("numbers.query_text", rel, "query file is missing"))
            continue
        found = re.search(rule.pattern, sql, re.I) is not None
        if rule.must_match and not found:
            out.append(Finding("numbers.query_text", rel,
                               f"executable SQL lacks /{rule.pattern}/ - {rule.why}"))
        elif not rule.must_match and found:
            out.append(Finding("numbers.query_text", rel,
                               f"executable SQL contains /{rule.pattern}/ - {rule.why}"))
    # De-duplicate identical findings from repeated rules.
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq
