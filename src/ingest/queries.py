"""Query loading, parameter substitution, and month chunking.

SuiteQL through the MCP connector takes one literal SQL string. There are no
bind parameters. So the `:name` placeholders in src/data/queries/*.sql are
substituted HERE, in Python, before the text ever reaches the tool - and the
only thing standing between a malformed value and the ledger is this module.
Hence the rules, which are deliberately narrow:

  dates     must be YYYY-MM-DD and must parse; emitted as a quoted literal,
            so `TO_DATE(:date_from, 'YYYY-MM-DD')` becomes
            `TO_DATE('2026-08-01', 'YYYY-MM-DD')`.
  integers  ids (subsidiary, category) are emitted bare.
  strings   only [A-Za-z0-9_\\-:. ] is allowed, then single-quoted. A quote,
            a semicolon, a comment marker, a newline - refused. We have no
            legitimate parameter that needs them, so the allowlist costs
            nothing and closes the door.

Every placeholder in the SQL must be supplied and every supplied parameter
must be used. The second rule catches the typo where `date_too` is passed,
`date_to` stays a literal ':date_to', and SuiteQL returns an error thirty
seconds later that names neither.

The query hash is taken over the comment-stripped, UN-substituted statement.
Editing a comment does not change it; changing a clause does; pulling a
different month does not. That is what src.freeze records in _meta so a
later reader can tell "the data moved" from "the query moved". (Snapshots
written before this module existed hashed the raw file text, comments and
all, so their hashes differ from what load_query reports for the same SQL.
That is a one-time discontinuity, not drift.)

Files may hold several statements separated by `;` (geography_12mo.sql has
three). SuiteQL runs one statement per call, so a multi-statement file needs
an explicit `statement=` index.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple

from ..freeze import SnapshotStore, query_hash
from ..periods import PeriodState, classify, month_start, shift_month

__all__ = [
    "Query", "QueryError", "ParameterError", "DATA_QUERIES", "INGEST_QUERIES", "SEARCH_PATH",
    "load_query", "strip_comments", "statements", "substitute", "render_value",
    "find_query", "month_bounds", "months_to_pull",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_QUERIES = REPO_ROOT / "src" / "data" / "queries"
# Queries that only ingest needs (the cohort M1 and revenue-to-date
# companions documented in cohorts_m13.sql) live beside the adapters until
# the data layer adopts them. Same loader, same rules.
INGEST_QUERIES = Path(__file__).resolve().parent / "queries"
SEARCH_PATH: tuple[Path, ...] = (DATA_QUERIES, INGEST_QUERIES)

_PLACEHOLDER = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")
_SAFE_STRING = re.compile(r"^[A-Za-z0-9_\-:. ]+$")
_DATE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INTEGER = re.compile(r"^-?\d+$")


class QueryError(ValueError):
    pass


class ParameterError(QueryError):
    pass


class Query(NamedTuple):
    """Unpacks as (sql, query_hash) - the shape the adapters and store want."""

    sql: str
    query_hash: str


# ---------------------------------------------------------------------------
# Text handling
# ---------------------------------------------------------------------------

def _split_outside_quotes(text: str, marker: str) -> list[str]:
    """Split on `marker` wherever it is not inside a single-quoted literal."""
    parts, buf, in_quote, i = [], [], False, 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            in_quote = not in_quote
        if not in_quote and text.startswith(marker, i):
            parts.append("".join(buf))
            buf = []
            i += len(marker)
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def strip_comments(sql: str) -> str:
    """Remove `--` comments (whole-line and trailing) and blank lines.

    A `--` inside a quoted literal is left alone. Assertions and hashes must
    apply to what runs, not to what the comments promise.
    """
    out = []
    for line in sql.splitlines():
        code = _split_outside_quotes(line, "--")[0].rstrip()
        if code.strip():
            out.append(code)
    return "\n".join(out)


def statements(sql: str) -> list[str]:
    """Statements in a comment-stripped file, split on `;` outside quotes."""
    return [s.strip() for s in _split_outside_quotes(sql, ";") if s.strip()]


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

def render_value(name: str, value: Any) -> str:
    """The SQL literal for one parameter, or ParameterError."""
    if isinstance(value, bool):
        raise ParameterError(f":{name}: bool is not a SQL parameter")
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ParameterError(f":{name}: only integer ids and dates are substituted, got {value}")
        return str(int(value))
    if not isinstance(value, str):
        raise ParameterError(f":{name}: unsupported parameter type {type(value).__name__}")
    s = value.strip()
    if not s:
        raise ParameterError(f":{name}: empty value")
    if _INTEGER.match(s):
        return str(int(s))
    if _DATE_SHAPE.match(s):
        try:
            return f"'{date.fromisoformat(s).isoformat()}'"
        except ValueError:
            raise ParameterError(f":{name}: {s!r} looks like a date but is not a valid YYYY-MM-DD") from None
    if "--" in s or not _SAFE_STRING.match(s):
        raise ParameterError(
            f":{name}: value {value!r} contains characters outside [A-Za-z0-9_-:. ]; "
            f"quotes, semicolons, comment markers and newlines are never substituted into SQL"
        )
    return f"'{s}'"


def substitute(sql: str, params: Mapping[str, Any]) -> str:
    """Replace every :placeholder; refuse missing or unused parameters."""
    wanted = set(_PLACEHOLDER.findall(sql))
    missing = sorted(wanted - set(params))
    unused = sorted(set(params) - wanted)
    if missing:
        raise ParameterError(f"SQL needs parameters {missing} that were not supplied")
    if unused:
        raise ParameterError(
            f"parameters {unused} are not referenced by the SQL (placeholders present: {sorted(wanted)}); "
            f"a misspelt name would otherwise leave a literal ':name' in the query"
        )
    rendered = {n: render_value(n, v) for n, v in params.items()}
    return _PLACEHOLDER.sub(lambda m: rendered[m.group(1)], sql)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def find_query(name: str, search_path: Iterable[Path] = SEARCH_PATH) -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise QueryError(f"query name {name!r} must be a bare file stem, not a path")
    for root in search_path:
        p = root / f"{name}.sql"
        if p.exists():
            return p
    raise QueryError(f"no query {name}.sql in {[str(r) for r in search_path]}")


def load_query(name: str, params: Mapping[str, Any] | None = None, *,
               statement: int | None = None,
               search_path: Iterable[Path] = SEARCH_PATH) -> Query:
    """(sql, query_hash) for src/data/queries/<name>.sql with :params filled in.

    The hash covers the comment-stripped statement BEFORE substitution, so it
    identifies the query, not the month it was run for.
    """
    text = find_query(name, search_path).read_text()
    stmts = statements(strip_comments(text))
    if not stmts:
        raise QueryError(f"{name}.sql contains no executable SQL")
    if statement is None:
        if len(stmts) != 1:
            raise QueryError(
                f"{name}.sql holds {len(stmts)} statements; SuiteQL runs one per call, "
                f"pass statement=0..{len(stmts) - 1}"
            )
        template = stmts[0]
    else:
        if not 0 <= statement < len(stmts):
            raise QueryError(f"{name}.sql has statements 0..{len(stmts) - 1}, not {statement}")
        template = stmts[statement]
    return Query(substitute(template, params or {}), query_hash(template))


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def month_bounds(month: str) -> tuple[date, date]:
    """Half-open [first day of month, first day of next month)."""
    return month_start(month), month_start(shift_month(month, 1))


def months_to_pull(store: SnapshotStore, domain: str, months: Iterable[str], as_of: date) -> list[str]:
    """The months a live pull may write: OPEN, or CLOSED but not yet frozen.

    A FROZEN month is never in the list. The store would refuse the write
    anyway, but not asking the source for it saves a 180-second query and,
    more to the point, keeps "we re-pulled a published month" from ever
    being an accident.
    """
    out = []
    for m in months:
        frozen = store.exists(m, domain) and store.read(m, domain).frozen
        if classify(m, as_of, frozen).state is not PeriodState.FROZEN:
            out.append(m)
    return out
