"""Chart specs as data. No chart is configured in JavaScript.

v1 wrote a `new Chart(...)` block per chart, per page - fourteen copies of
the same options object, each drifting. Two of those drifts were bugs:

- A hand-set `max` on the y axis was lower than the data, so the tallest
  bar was silently cut off at the top of the plot. Nobody noticed because
  the bar looked full. `chart_spec` refuses to build a spec whose data
  exceeds an explicit scale max.
- The channel-mix donut. Twelve slices, six of them under 3%, unreadable
  and unlabelled. Donuts are not available here; the message says what to
  draw instead.

The spec this module returns is JSON-shaped (dict/list/str/Decimal/bool)
and is embedded in the page by templates/components/chart.html. The one
bootstrap script in base.html turns each payload into a Chart.js instance
and applies axis-tick formatting from `meta.y_format`, so number formatting
in charts is also declared, not coded.

Numbers are Decimal. A float is refused at the door.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from .brand import BRAND

__all__ = ["chart_spec", "ChartClippingError", "ChartSpecError", "Y_FORMATS", "KINDS"]

KINDS = frozenset({"bar", "hbar", "line"})
Y_FORMATS = frozenset({"usd", "count", "pct"})
_FORBIDDEN = {"donut", "doughnut", "pie", "polararea", "polar", "radar"}


class ChartSpecError(ValueError):
    pass


class ChartClippingError(ChartSpecError):
    """A dataset value exceeds the explicit scale max - the bar would be cut off."""


def _d(x: Any, where: str) -> Decimal:
    if isinstance(x, bool):
        raise TypeError(f"{where}: bool is not a chart value")
    if isinstance(x, Decimal):
        return x
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):
        raise TypeError(
            f"{where}: float {x!r} refused. Every number is Decimal; convert at the source "
            f"with Decimal(str(x)) if it genuinely originated as a float."
        )
    if isinstance(x, str):
        return Decimal(x)
    if hasattr(x, "amount"):     # Money
        return x.amount
    if hasattr(x, "value"):      # Pct / Ratio
        return x.value
    if hasattr(x, "n"):          # Count
        return Decimal(x.n)
    raise TypeError(f"{where}: cannot use {type(x).__name__} as a chart value")


def _normalise_series(series: Any, n_labels: int) -> list[dict[str, Any]]:
    """Accept a bare list of values or a list of {label, values} dicts."""
    if not series:
        raise ChartSpecError("a chart needs at least one series")
    if not isinstance(series[0], dict):
        series = [{"label": "", "values": list(series)}]
    if len(series) > len(BRAND.series_ramp):
        raise ChartSpecError(
            f"{len(series)} series exceeds the single-hue ramp of {len(BRAND.series_ramp)}; "
            f"split into two charts rather than adding a second hue"
        )
    out = []
    for i, s in enumerate(series):
        vals = [_d(v, f"series[{i}][{j}]") for j, v in enumerate(s["values"])]
        if len(vals) != n_labels:
            raise ChartSpecError(
                f"series[{i}] has {len(vals)} values for {n_labels} labels"
            )
        out.append({"label": str(s.get("label", "")), "values": vals})
    return out


def chart_spec(kind: str, labels: Sequence[str], series: Any, *,
               emphasis_index: int | None = None, y_format: str = "usd",
               scale_max: Any = None, title: str | None = None,
               begin_at_zero: bool = True) -> dict[str, Any]:
    """Build a Chart.js config as plain data.

    kind            'bar' | 'hbar' | 'line'. Anything donut-shaped raises.
    labels          x-axis labels (month names, channel names...). Strings.
    series          a list of values, or up to three {label, values} dicts.
    emphasis_index  ONE bar in the first series painted Yellow - the month
                    being reported, the channel under discussion. Not for
                    line charts, where a single yellow point reads as an error.
    y_format        'usd' | 'count' | 'pct'. Applied by the page bootstrap.
    scale_max       an explicit axis ceiling. If any value exceeds it the
                    spec is refused (the v1 clipping bug). None = autoscale.
    """
    k = str(kind).lower()
    if k in _FORBIDDEN:
        raise NotImplementedError(
            f"{kind!r} charts are not available. Draw the same data as bars sorted by value "
            f"(chart_spec('hbar', ...) with labels and values sorted descending): a reader can "
            f"compare bar lengths and read every label; a donut with twelve slices hides both."
        )
    if k not in KINDS:
        raise ChartSpecError(f"kind must be one of {sorted(KINDS)}, not {kind!r}")
    if y_format not in Y_FORMATS:
        raise ChartSpecError(f"y_format must be one of {sorted(Y_FORMATS)}, not {y_format!r}")
    labels = [str(l) for l in labels]
    if not labels:
        raise ChartSpecError("a chart needs at least one label")
    norm = _normalise_series(series, len(labels))

    ceiling = None if scale_max is None else _d(scale_max, "scale_max")
    if ceiling is not None:
        for i, s in enumerate(norm):
            for j, v in enumerate(s["values"]):
                if v > ceiling:
                    raise ChartClippingError(
                        f"series[{i}] ({s['label'] or 'unnamed'}) value {v} at {labels[j]!r} exceeds "
                        f"scale_max {ceiling}; the bar would be clipped. Raise the max or drop it "
                        f"and let the axis autoscale."
                    )

    if emphasis_index is not None:
        if k == "line":
            raise ChartSpecError("emphasis_index is for bars; a single yellow point on a line reads as an error")
        if not isinstance(emphasis_index, int) or not (0 <= emphasis_index < len(labels)):
            raise ChartSpecError(f"emphasis_index {emphasis_index!r} is outside 0..{len(labels) - 1}")

    datasets = []
    for i, s in enumerate(norm):
        base = BRAND.series_ramp[i]
        ds: dict[str, Any] = {"label": s["label"], "data": s["values"]}
        if k == "line":
            ds.update({
                "borderColor": base, "backgroundColor": BRAND.aqua_alpha("0.10"),
                "borderWidth": 2, "pointRadius": 3, "pointBackgroundColor": base,
                "tension": 0, "fill": i == 0,
            })
        else:
            if i == 0 and emphasis_index is not None:
                colours = [base] * len(labels)
                colours[emphasis_index] = BRAND.yellow
                ds["backgroundColor"] = colours
            else:
                ds["backgroundColor"] = base
            ds["borderRadius"] = 2
            ds["maxBarThickness"] = 48
        datasets.append(ds)

    value_axis = "x" if k == "hbar" else "y"
    category_axis = "y" if k == "hbar" else "x"
    scales: dict[str, Any] = {
        value_axis: {"beginAtZero": begin_at_zero, "grid": {"color": "#E3E6EA"},
                     "ticks": {"color": BRAND.grey}},
        category_axis: {"grid": {"display": False}, "ticks": {"color": BRAND.grey}},
    }
    if ceiling is not None:
        scales[value_axis]["max"] = ceiling

    return {
        "type": "line" if k == "line" else "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "indexAxis": "y" if k == "hbar" else "x",
            "responsive": True,
            "maintainAspectRatio": False,
            "animation": False,                       # brand: no animation
            "plugins": {
                # One series needs no legend: the title says what it is.
                "legend": {"display": len(datasets) > 1,
                           "labels": {"boxWidth": 12, "color": BRAND.deep}},
                "title": {"display": bool(title), "text": title or "",
                          "color": BRAND.deep, "align": "start",
                          "font": {"weight": "bold"}},
            },
            "scales": scales,
        },
        # Read by the bootstrap in base.html, not by Chart.js.
        "meta": {
            "y_format": y_format,
            "value_axis": value_axis,
            "emphasis_index": emphasis_index,
            "scale_max": ceiling,
        },
    }
