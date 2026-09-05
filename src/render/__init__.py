"""The render layer: every displayed figure is traceable, or it is not displayed.

v1 had numbers typed into HTML by hand - 82 tiles across seven pages, and
when a figure was corrected in one place it stayed wrong in the others. Here
nothing numeric reaches a template except through `MetricRegistry`:

    registry.register("aug26.new_customers", Count(72, "2026-08"), kind="count",
                      period="2026-08", source="netsuite:cohorts_m1")
    ...
    {{ m("aug26.new_customers") }}   ->   <span data-metric="aug26.new_customers" ...>72</span>

The span attributes are what make the validation layer possible: it can walk
a rendered page, find every figure, and check it against the registry and
against the same figure on the other dashboards. A digit outside one of
those spans is a build failure.

Public surface:

    MetricRegistry, RenderedMetric, RenderedDelta, RenderedClaim   (registry.py)
    make_env, render                                               (env.py)
    chart_spec, ChartClippingError                                 (charts.py)
    BRAND                                                          (brand.py)
    EXECUTIVE                                                      (contracts.py)
"""

from .brand import BRAND
from .charts import ChartClippingError, chart_spec
from .contracts import EXECUTIVE, PageContract
from .env import make_env, render
from .registry import (
    ClaimError,
    MetricRegistry,
    RenderedClaim,
    RenderedDelta,
    RenderedMetric,
    RegistryError,
)

__all__ = [
    "BRAND",
    "ChartClippingError",
    "ClaimError",
    "EXECUTIVE",
    "MetricRegistry",
    "PageContract",
    "RegistryError",
    "RenderedClaim",
    "RenderedDelta",
    "RenderedMetric",
    "chart_spec",
    "make_env",
    "render",
]
