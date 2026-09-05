"""Types shared by every adapter.

`Pull` is what an adapter returns: the snapshot body, the row count the
source handed back (recorded in _meta so a later reader can see how much
data stood behind the figure), and the query fingerprint.

`jsonable` exists because the snapshot store serialises with json.dumps and
JSON has no decimal type. Every number inside an adapter is Decimal; on the
way to disk an integral Decimal becomes an int and a fractional one becomes a
JSON number ONLY if the float carrier round-trips to the identical Decimal
(repr(float) is the shortest string that does, so `Decimal(repr(f)) == d` is
an exact test). Anything that would lose a digit is written as a string,
which every reader already handles because they all do Decimal(str(x)).
No arithmetic ever happens on the carrier. A float arriving from a source
is refused: the executor must hand over strings or Decimals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NamedTuple

__all__ = ["Pull", "MissingManualInput", "dec", "jsonable", "month_label", "IngestError"]


class IngestError(RuntimeError):
    """Base class for every refusal an adapter can issue."""


class Pull(NamedTuple):
    """One adapter result. Unpacks as (body, row_count, query_hash)."""

    body: dict
    row_count: int
    query_hash: str


@dataclass(frozen=True)
class MissingManualInput:
    """A manual file that has not been supplied yet.

    Returned, not raised: GMB and Hotjar arrive by hand and the build must
    render a "data pending" callout for them rather than fail. `reason` is
    the sentence that callout shows.
    """

    domain: str
    month: str
    expected: tuple[Path, ...]

    @property
    def reason(self) -> str:
        names = " or ".join(p.name for p in self.expected)
        folder = self.expected[0].parent
        try:
            folder = folder.relative_to(Path(__file__).resolve().parents[2])
        except ValueError:
            pass
        return (f"{self.domain.upper()} figures for {month_label(self.month)} have not been "
                f"supplied yet. Add {names} under {folder}/ and rebuild.")


def dec(value: Any, where: str = "value") -> Decimal:
    """Decimal from a source value. Floats are refused; strings are exact."""
    if isinstance(value, bool):
        raise TypeError(f"{where}: a bool is not a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        raise TypeError(
            f"{where}: float {value!r} refused. Numbers cross the executor boundary as strings "
            f"or Decimals so no digit is lost; the MCP result is JSON text, keep it that way."
        )
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{where}: empty value where a number was expected")
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{where}: {value!r} is not a number") from None


def jsonable(value: Any) -> Any:
    """Prepare a body for json.dumps without losing a digit (see module doc)."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        f = float(value)
        return f if Decimal(repr(f)) == value else str(value)
    if isinstance(value, float):
        raise TypeError("a float reached a snapshot body; every number is Decimal")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def month_label(month: str) -> str:
    """'2026-08' -> 'August 2026'."""
    y, m = month.split("-")
    return f"{_MONTHS[int(m) - 1]} {y}"
