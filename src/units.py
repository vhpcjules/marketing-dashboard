"""Units, formatting, and the single delta function.

This module exists because of one bug class that recurred three times in v1:
a percentage-point difference was computed and printed with a percent sign.
Phone capture moving 45.6% -> 55.7% rendered as "+10.1%" when the relative
change is +22.1%.

Two defences live here:

1. There is exactly ONE delta function in the codebase: `delta()`. The
   validation layer asserts this by AST scan. Do not write another.

2. Units are types. Subtracting two `Pct` values yields `PctPoints`, whose
   __format__ raises. A point difference therefore cannot reach a template
   wearing a percent sign - the build dies instead.

Money is Decimal throughout. Cross-dashboard agreement is required to the
cent, and floats will eventually cost us that.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Union

Number = Union[int, float, Decimal]

__all__ = [
    "delta",
    "Money",
    "Pct",
    "PctPoints",
    "Ratio",
    "Count",
    "PointDifferenceError",
    "UndefinedDeltaError",
]


class PointDifferenceError(TypeError):
    """Raised when a percentage-point difference is asked to render itself.

    A point difference is almost never what we want to publish. If you are
    holding one of these, you either wanted `delta()` (a relative change) or
    you wanted to state the range: "36.9% -> 52.0%".
    """


class UndefinedDeltaError(ZeroDivisionError):
    """Raised when a delta is requested against a zero baseline.

    There is no honest relative change from zero. Callers must handle this
    explicitly - usually by rendering "new" or an em dash - rather than
    silently receiving 0, 100, or infinity.
    """


def _dec(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# THE delta function. One. Only.
# ---------------------------------------------------------------------------

def delta(current: Number, previous: Number) -> Decimal:
    """Relative percent change from `previous` to `current`.

        (current - previous) / previous * 100

    This is the only correct delta in this codebase, and it applies even when
    the metric is itself a percentage: 45.6% -> 55.7% is +22.1%, never
    "+10.1 points".

    Raises UndefinedDeltaError when `previous` is zero, so the caller has to
    decide what "change from nothing" should say.
    """
    cur, prev = _dec(current), _dec(previous)
    if prev == 0:
        raise UndefinedDeltaError(
            f"no relative change is defined from a zero baseline "
            f"(current={current!r}); handle this case explicitly"
        )
    return (cur - prev) / prev * Decimal(100)


# ---------------------------------------------------------------------------
# Unit types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class Money:
    """A currency amount, always with the period it belongs to.

    The period label is not decoration. Section 7.2 requires every currency
    figure on every dashboard to carry a time window, because M1, M1-3 and
    lifetime figures are not interchangeable. Making it a constructor
    argument means an unlabelled currency figure cannot be built.
    """

    amount: Decimal
    period: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _dec(self.amount))
        if not self.period or not str(self.period).strip():
            raise ValueError(
                "Money requires a period label (e.g. '2026-07', 'Jan-Jul 2026', "
                "'M1-3 of the 2026-04 cohort'). Unlabelled currency is a v1 bug."
            )

    def __add__(self, other: "Money") -> "Money":
        self._same_period(other, "add")
        return Money(self.amount + other.amount, self.period)

    def __sub__(self, other: "Money") -> "Money":
        self._same_period(other, "subtract")
        return Money(self.amount - other.amount, self.period)

    def _same_period(self, other: "Money", verb: str) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot {verb} {type(other).__name__} and Money")
        if other.period != self.period:
            raise ValueError(
                f"refusing to {verb} amounts from different periods: "
                f"{self.period!r} and {other.period!r}"
            )

    @staticmethod
    def _signed(q: Decimal, suffix: str = "") -> str:
        # A negative amount reads "−$11,929", never "$-11,929". Real minus sign,
        # matching the delta arrows, so a table column of signed variances lines up.
        return f"−${abs(q):,}{suffix}" if q < 0 else f"${q:,}{suffix}"

    @property
    def usd0(self) -> str:
        return self._signed(self.amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @property
    def usd2(self) -> str:
        return self._signed(self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @property
    def usdk(self) -> str:
        return self._signed((self.amount / 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP), "K")

    def __format__(self, spec: str) -> str:
        return self.usd0 if not spec else format(self.amount, spec)

    def __str__(self) -> str:
        return self.usd0


@dataclass(frozen=True, order=True)
class Pct:
    """A percentage, as a number out of 100. `Pct(55.7)` is 55.7%."""

    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _dec(self.value))

    def __sub__(self, other: "Pct") -> "PctPoints":
        """Subtraction yields PctPoints, which refuses to render.

        This is the guard rail. If you want the change between two
        percentages, call delta() - which is what we publish.
        """
        if not isinstance(other, Pct):
            raise TypeError(f"cannot subtract {type(other).__name__} from Pct")
        return PctPoints(self.value - other.value, minuend=self, subtrahend=other)

    __add__ = None  # type: ignore[assignment]  # adding percentages is nearly always wrong

    @property
    def pct1(self) -> str:
        return f"{self.value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"

    @property
    def pct0(self) -> str:
        return f"{self.value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)}%"

    def __format__(self, spec: str) -> str:
        return self.pct1 if not spec else format(self.value, spec)

    def __str__(self) -> str:
        return self.pct1


@dataclass(frozen=True)
class PctPoints:
    """A percentage-point difference. Deliberately unrenderable.

    Section 4.6: "Never display a percentage-point difference." Every attempt
    to format one of these raises, and the message tells the author what to do
    instead.
    """

    value: Decimal
    minuend: Pct | None = None
    subtrahend: Pct | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _dec(self.value))

    def _refuse(self) -> "PointDifferenceError":
        hint = ""
        if self.minuend is not None and self.subtrahend is not None:
            try:
                rel = delta(self.minuend.value, self.subtrahend.value)
                rel_s = f"{rel.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
            except UndefinedDeltaError:
                rel_s = "undefined (zero baseline)"
            hint = (
                f" You have {self.value} points. The publishable figures are the "
                f"relative change {rel_s} via delta(), or the range "
                f"'{self.subtrahend.pct1} -> {self.minuend.pct1}'."
            )
        return PointDifferenceError(
            "refusing to render a percentage-point difference." + hint
        )

    def __format__(self, spec: str) -> str:
        raise self._refuse()

    def __str__(self) -> str:
        raise self._refuse()

    def __repr__(self) -> str:
        # repr stays usable so debuggers and tracebacks work.
        return f"PctPoints({self.value})"

    @property
    def points(self) -> Decimal:
        """Escape hatch for genuine internal arithmetic. Never for display."""
        return self.value


@dataclass(frozen=True, order=True)
class Ratio:
    """A multiple, e.g. return per dollar ($3.71) or an M1-3 multiple (1.52x)."""

    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _dec(self.value))

    @property
    def per_dollar(self) -> str:
        return f"${self.value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"

    @property
    def multiple(self) -> str:
        return f"{self.value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}x"

    def __format__(self, spec: str) -> str:
        return self.per_dollar if not spec else format(self.value, spec)


@dataclass(frozen=True, order=True)
class Count:
    """A whole number of things, with the period it counts over."""

    n: int
    period: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "n", int(self.n))
        if not self.period or not str(self.period).strip():
            raise ValueError("Count requires a period label")

    @property
    def plain(self) -> str:
        return f"{self.n:,}"

    def __format__(self, spec: str) -> str:
        return self.plain if not spec else format(self.n, spec)

    def __str__(self) -> str:
        return self.plain


# ---------------------------------------------------------------------------
# Direction / colour agreement
# ---------------------------------------------------------------------------

def direction_class(change_pct: Number, higher_is_better: bool = True) -> str:
    """CSS class for a delta, derived from the delta itself.

    v1 styled a 63% fall in average deal size green. Colour is computed here
    from the same number that is displayed, so the two cannot disagree - and
    `higher_is_better=False` covers metrics like cost per customer and CPM
    where a rise is bad news.
    """
    change = _dec(change_pct)
    if change == 0:
        return "delta-flat"
    good = (change > 0) if higher_is_better else (change < 0)
    return "delta-good" if good else "delta-bad"


def arrow(change_pct: Number) -> str:
    change = _dec(change_pct)
    return "↑" if change > 0 else ("↓" if change < 0 else "→")
