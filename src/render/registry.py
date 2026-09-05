"""MetricRegistry: the one door a number walks through to reach a page.

Why a registry and not just `{{ value }}`:

1. Traceability. Every displayed figure is wrapped in
   <span data-metric="ID" data-kind="KIND" data-period="PERIOD">, so the
   validation layer can find every number on a rendered page, look it up,
   and compare it with the same ID on another dashboard. v1 had the same
   figure typed by hand on three pages and it disagreed on two of them.

2. The reverse-orphan report. A metric that was computed and registered but
   never displayed is either a page that lost a tile or a computation that
   nobody needed. `unused()` lists them; the build prints the list.

3. Units stay units until the last moment. The registry accepts only
   src.units types (or str for prose), and formatting happens inside
   `RenderedMetric` by calling the unit's own formatter. There is no number
   formatting in this module - and deliberately no second delta function:
   `delta_between` calls `src.units.delta`, the only one.

Metric ID convention (documented here because templates use it verbatim):

    <period>.<measure>[.<qualifier>]

    period    mmmyy      one calendar month           aug26.new_customers
              ytdYY      Jan..reporting month         ytd26.spend
              fyYY       full fiscal year             fy25.m1_net
              r12        rolling 12 closed months     r12.sources.organic
              m13        latest CLOSED 90-day cohort  m13.latest.multiple
    measure   snake_case, the same word the methodology uses (m1_net, not
              "revenue"; new_customers, not "customers").

Claims are the prose counterpart: a derived statement ("repeat revenue is
46.4% of everything those cohorts produced") registered with a callable that
produces it and an optional assertion that must hold. <span data-claim="ID">
marks it in the page the same way data-metric marks a figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Iterable

from markupsafe import Markup, escape

from ..units import Count, Money, Pct, PctPoints, Ratio, arrow, delta, direction_class

__all__ = [
    "KINDS", "MetricRegistry", "Registered", "RenderedClaim", "RenderedDelta",
    "RenderedMetric", "RegistryError", "ClaimError",
]

KINDS = frozenset({"currency", "pct", "ratio", "count", "text"})

# kind -> (accepted value type, default formatter attribute on that type)
_KIND_TYPES: dict[str, tuple[type, str | None]] = {
    "currency": (Money, "usd0"),
    "pct": (Pct, "pct1"),
    "ratio": (Ratio, "per_dollar"),
    "count": (Count, "plain"),
    "text": (str, None),
}

# Formatters a caller may choose at registration time. All of them live on
# the unit types in src.units; the registry never formats a number itself.
_ALLOWED_FMT: dict[str, frozenset[str]] = {
    "currency": frozenset({"usd0", "usd2", "usdk"}),
    "pct": frozenset({"pct1", "pct0"}),
    "ratio": frozenset({"per_dollar", "multiple"}),
    "count": frozenset({"plain"}),
    "text": frozenset(),
}


class RegistryError(KeyError):
    """A metric or claim ID the registry does not know.

    KeyError subclass so `except KeyError` still works; the message says
    which IDs are close, because the usual cause is a typo in a template.
    """

    def __str__(self) -> str:  # KeyError quotes its message; we want it readable
        return str(self.args[0]) if self.args else ""


class ClaimError(AssertionError):
    """A claim's assertion did not hold when it was evaluated for display."""


def _raw(value: Any) -> Decimal:
    """The Decimal behind a unit type - for delta and totals only."""
    if isinstance(value, Money):
        return value.amount
    if isinstance(value, Pct):
        return value.value
    if isinstance(value, Ratio):
        return value.value
    if isinstance(value, Count):
        return Decimal(value.n)
    raise TypeError(f"{type(value).__name__} has no numeric value to compare")


@dataclass(frozen=True)
class Registered:
    metric_id: str
    value: Any
    kind: str
    period: str
    source: str
    higher_is_better: bool
    note: str | None
    fmt: str | None

    @property
    def text(self) -> str:
        if self.kind == "text":
            return self.value
        attr = self.fmt or _KIND_TYPES[self.kind][1]
        return getattr(self.value, attr)


@dataclass(frozen=True)
class RenderedMetric:
    """A figure ready for a template. `str()` and `__html__` both give the span."""

    metric_id: str
    kind: str
    period: str
    text: str
    higher_is_better: bool
    value: Any = field(repr=False)

    @property
    def html(self) -> Markup:
        hib = " data-higher-is-better" if self.higher_is_better else ""
        return Markup(
            f'<span data-metric="{escape(self.metric_id)}" data-kind="{escape(self.kind)}" '
            f'data-period="{escape(self.period)}"{hib}>{escape(self.text)}</span>'
        )

    def __html__(self) -> str:
        return str(self.html)

    def __str__(self) -> str:
        return str(self.html)


@dataclass(frozen=True)
class RenderedDelta:
    """A relative change between two registered metrics, with its colour class.

    `css_class` is derived from `change` by src.units.direction_class, so the
    colour and the number cannot disagree (v1 styled a 63% fall green).
    """

    metric_id: str          # "<current_id>__delta"
    change: Decimal
    css_class: str
    higher_is_better: bool = True   # carried into the markup so the gate can check colour vs direction

    @property
    def change_1dp(self) -> Decimal:
        return self.change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @property
    def signed(self) -> str:
        return f"{self.change_1dp:+}"

    @property
    def text(self) -> str:
        return f"{arrow(self.change)} {abs(self.change_1dp)}%"

    @property
    def html(self) -> Markup:
        hib = "" if self.higher_is_better else ' data-higher-is-better="false"'
        return Markup(
            f'<span class="delta {self.css_class}" data-delta="{self.signed}"{hib} '
            f'data-metric="{escape(self.metric_id)}">{escape(self.text)}</span>'
        )

    def __html__(self) -> str:
        return str(self.html)

    def __str__(self) -> str:
        return str(self.html)


@dataclass(frozen=True)
class RenderedClaim:
    claim_id: str
    text: str

    @property
    def html(self) -> Markup:
        return Markup(f'<span data-claim="{escape(self.claim_id)}">{escape(self.text)}</span>')

    def __html__(self) -> str:
        return str(self.html)

    def __str__(self) -> str:
        return str(self.html)


@dataclass(frozen=True)
class _Claim:
    claim_id: str
    expr: Callable[[], Any]
    assert_fn: Callable[[Any], bool] | None
    render: Callable[[Any], str] | None


class MetricRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, Registered] = {}
        self._claims: dict[str, _Claim] = {}
        self._accessed: set[str] = set()

    # -- registration -----------------------------------------------------

    def register(self, metric_id: str, value: Any, *, kind: str, period: str | None = None,
                 source: str, higher_is_better: bool = True, note: str | None = None,
                 fmt: str | None = None) -> Registered:
        """Register one displayable figure.

        `value` must be a src.units type matching `kind`, or a str for
        kind='text'. A raw Decimal or float is refused: an unlabelled number
        has no period and no formatter, which is exactly the v1 failure.

        `period` may be omitted for Money and Count, which already carry
        one; when given it wins, so a display label ("Jan–Aug 2026") can
        stand in for the machine label ("2026-01..2026-08").
        """
        if not metric_id or not isinstance(metric_id, str):
            raise ValueError("metric_id must be a non-empty string")
        if metric_id in self._metrics:
            raise ValueError(f"metric {metric_id!r} is already registered; IDs are unique per build")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}, not {kind!r}")
        if isinstance(value, PctPoints):
            # Belt and braces: PctPoints already refuses to format, but the
            # error should name the registration, not the template.
            raise TypeError(
                f"metric {metric_id!r}: a percentage-point difference cannot be registered. "
                f"Use delta_between() for the relative change, or register both ends as a range."
            )
        want_type, _ = _KIND_TYPES[kind]
        if not isinstance(value, want_type):
            raise TypeError(
                f"metric {metric_id!r}: kind={kind!r} requires a {want_type.__name__}, "
                f"got {type(value).__name__}. Numbers reach the registry as src.units types only."
            )
        if fmt is not None and fmt not in _ALLOWED_FMT[kind]:
            raise ValueError(
                f"metric {metric_id!r}: fmt {fmt!r} is not a {kind} formatter; "
                f"choose from {sorted(_ALLOWED_FMT[kind])}"
            )
        if period is None:
            period = getattr(value, "period", None)
        if not period or not str(period).strip():
            raise ValueError(
                f"metric {metric_id!r}: a period label is required (Section 7.2 - every figure "
                f"carries its time window). Pct, Ratio and text values do not carry one."
            )
        if not source or not str(source).strip():
            raise ValueError(f"metric {metric_id!r}: source is required (e.g. 'netsuite:cohorts_m1')")
        reg = Registered(metric_id, value, kind, str(period), source, bool(higher_is_better), note, fmt)
        self._metrics[metric_id] = reg
        return reg

    def register_claim(self, claim_id: str, expr: Callable[[], Any], assert_fn=None,
                       render: Callable[[Any], str] | None = None) -> None:
        """Register a derived statement in prose.

        `expr` is evaluated when the claim is displayed. If `assert_fn` is
        given it must return True for the value or the build dies with
        ClaimError - a claim that stopped being true must not stay on the
        page because nobody re-read the sentence.
        """
        if not claim_id or not isinstance(claim_id, str):
            raise ValueError("claim_id must be a non-empty string")
        if claim_id in self._claims or claim_id in self._metrics:
            raise ValueError(f"claim {claim_id!r} is already registered")
        if not callable(expr):
            raise TypeError(f"claim {claim_id!r}: expr must be callable")
        self._claims[claim_id] = _Claim(claim_id, expr, assert_fn, render)

    # -- access -----------------------------------------------------------

    def _lookup(self, metric_id: str) -> Registered:
        try:
            return self._metrics[metric_id]
        except KeyError:
            raise RegistryError(self._missing_message(metric_id, "metric")) from None

    def _missing_message(self, wanted: str, what: str) -> str:
        pool = self._metrics if what == "metric" else self._claims
        stem = wanted.split(".")[0]
        near = sorted(k for k in pool if k.startswith(stem + ".") or wanted.split(".")[-1] in k)[:8]
        hint = f" Registered IDs with the same period or measure: {near}." if near else ""
        return (
            f"no {what} registered as {wanted!r}. The template asked for a figure the build "
            f"did not supply - register it, or fix the ID (convention: <period>.<measure>).{hint}"
        )

    def get(self, metric_id: str) -> Any:
        """The registered unit value. Records the access."""
        reg = self._lookup(metric_id)
        self._accessed.add(metric_id)
        return reg.value

    def entry(self, metric_id: str) -> Registered:
        """Metadata without recording an access (for validators and reports)."""
        return self._lookup(metric_id)

    def m(self, metric_id: str) -> RenderedMetric:
        reg = self._lookup(metric_id)
        self._accessed.add(metric_id)
        return RenderedMetric(reg.metric_id, reg.kind, reg.period, reg.text,
                              reg.higher_is_better, reg.value)

    def delta_between(self, cur_id: str, prev_id: str) -> RenderedDelta:
        """Relative change from prev to cur, via the one delta function.

        Both metrics must be the same non-text kind. Direction colour comes
        from the CURRENT metric's `higher_is_better`. A zero baseline raises
        UndefinedDeltaError from src.units - the page author must decide what
        "change from nothing" says; this function will not guess.
        """
        cur, prev = self._lookup(cur_id), self._lookup(prev_id)
        if cur.kind == "text" or prev.kind == "text":
            raise TypeError(f"delta_between({cur_id!r}, {prev_id!r}): text metrics have no delta")
        if cur.kind != prev.kind:
            raise TypeError(
                f"delta_between({cur_id!r}, {prev_id!r}): kinds differ ({cur.kind} vs {prev.kind}); "
                f"a change between unlike figures is not a number"
            )
        self._accessed.update((cur_id, prev_id))
        change = delta(_raw(cur.value), _raw(prev.value))
        return RenderedDelta(f"{cur_id}__delta", change,
                             direction_class(change, cur.higher_is_better),
                             cur.higher_is_better)

    def total(self, metric_id: str, parts: Iterable[Any], *, source: str = "computed:total",
              fmt: str | None = None) -> RenderedMetric:
        """Sum registered metrics (or RenderedMetrics) into a new registered metric.

        This is how table.html computes its own total row: the template hands
        over the cells, the sum happens on unit types (Money + Money refuses
        to cross periods), and the result is registered so it is traceable
        like every other figure. Only currency and count can be summed - a
        total of percentages or ratios is not a thing.
        """
        ids = [p.metric_id if isinstance(p, RenderedMetric) else p for p in parts]
        if not ids:
            raise ValueError(f"total {metric_id!r}: nothing to sum")
        regs = [self._lookup(i) for i in ids]
        kinds = {r.kind for r in regs}
        if len(kinds) != 1:
            raise TypeError(f"total {metric_id!r}: mixed kinds {sorted(kinds)} cannot be summed")
        kind = kinds.pop()
        if kind == "currency":
            acc = regs[0].value
            for r in regs[1:]:
                acc = acc + r.value        # Money.__add__ enforces one period
            value: Any = acc
        elif kind == "count":
            periods = {r.value.period for r in regs}
            if len(periods) != 1:
                raise ValueError(f"total {metric_id!r}: counts span periods {sorted(periods)}")
            value = Count(sum(r.value.n for r in regs), periods.pop())
        else:
            raise TypeError(f"total {metric_id!r}: cannot sum a column of kind {kind!r}")
        if metric_id in self._metrics:
            # Re-rendering the same table must not fail; but the sum must agree.
            existing = self._metrics[metric_id]
            if _raw(existing.value) != _raw(value):
                raise ValueError(f"total {metric_id!r} already registered with a different value")
        else:
            self.register(metric_id, value, kind=kind, period=regs[0].period, source=source,
                          higher_is_better=regs[0].higher_is_better, fmt=fmt or regs[0].fmt)
        return self.m(metric_id)

    def c(self, claim_id: str) -> RenderedClaim:
        try:
            claim = self._claims[claim_id]
        except KeyError:
            raise RegistryError(self._missing_message(claim_id, "claim")) from None
        self._accessed.add(claim_id)
        value = claim.expr()
        if claim.assert_fn is not None and not claim.assert_fn(value):
            raise ClaimError(
                f"claim {claim_id!r} evaluated to {value!r}, which fails its assertion; "
                f"the sentence built on it may no longer be true"
            )
        if claim.render is not None:
            text = claim.render(value)
        elif isinstance(value, (Money, Pct, Ratio, Count)):
            text = format(value)
        elif isinstance(value, PctPoints):
            text = str(value)          # raises PointDifferenceError, by design
        else:
            text = str(value)
        return RenderedClaim(claim_id, text)

    # -- reports ----------------------------------------------------------

    def ids(self) -> list[str]:
        return sorted(self._metrics)

    def claim_ids(self) -> list[str]:
        return sorted(self._claims)

    def accessed(self) -> list[str]:
        return sorted(self._accessed)

    def unused(self) -> list[str]:
        """Registered but never accessed - the reverse-orphan report."""
        everything = set(self._metrics) | set(self._claims)
        return sorted(everything - self._accessed)

    def manifest(self) -> list[dict[str, Any]]:
        """One row per metric, for the validation layer and the build log."""
        return [
            {
                "metric_id": r.metric_id, "kind": r.kind, "period": r.period,
                "source": r.source, "text": r.text, "higher_is_better": r.higher_is_better,
                "note": r.note, "accessed": r.metric_id in self._accessed,
            }
            for r in (self._metrics[k] for k in sorted(self._metrics))
        ]
