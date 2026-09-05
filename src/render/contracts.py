"""What each page needs from the registry, declared once.

A template with StrictUndefined fails on the first missing figure and stops.
That is the right behaviour at render time, but a build wants to know
everything that is missing at once, and the test suite wants to synthesise
a registry that satisfies the page without reading the template. So each
page declares its contract: the metric IDs (with kind), the claim IDs, the
chart keys and the table keys it will ask for.

`MetricRegistry` does not know about contracts. `check()` is a pure
comparison, so a page can be checked before a single template is loaded.

The executive contract is the reference for the ID convention described in
registry.py. Periods: aug26 is the reporting month, jul26 the month before,
ytd26 Jan–Aug 2026, ytd25 the same months of 2025, fy25/fy26 full years,
r12 the rolling twelve closed months, m13 the latest closed 90-day cohort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

__all__ = ["MetricSpec", "PageContract", "EXECUTIVE"]


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    kind: str
    higher_is_better: bool = True
    optional_section: str | None = None   # supplied only when that section is not pending


@dataclass(frozen=True)
class PageContract:
    template: str
    metrics: tuple[MetricSpec, ...]
    claims: tuple[str, ...]
    charts: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    pendable_sections: tuple[str, ...] = ()

    def required_metric_ids(self, pending: Iterable[str] = ()) -> list[str]:
        p = set(pending)
        return [s.metric_id for s in self.metrics
                if s.optional_section is None or s.optional_section not in p]

    def check(self, registered_ids: Iterable[str], registered_claims: Iterable[str],
              pending: Iterable[str] = ()) -> list[str]:
        """IDs the page will ask for that are not registered. Empty means go."""
        have = set(registered_ids) | set(registered_claims)
        missing = [i for i in self.required_metric_ids(pending) if i not in have]
        missing += [c for c in self.claims if c not in have]
        return missing


def _m(ids: Mapping[str, str], *, hib: bool = True, section: str | None = None) -> list[MetricSpec]:
    return [MetricSpec(i, k, hib, section) for i, k in ids.items()]


EXECUTIVE = PageContract(
    template="executive.html",
    metrics=tuple(
        # exec summary + growth
        _m({
            "aug26.new_customers": "count", "jul26.new_customers": "count",
            "aug26.m1_net": "currency", "jul26.m1_net": "currency",
            "aug26.avg_first_order": "currency", "jul26.avg_first_order": "currency",
            "aug26.m1_return_per_dollar": "ratio",
        })
        # first 90 days, latest closed cohort (pendable)
        + _m({
            "m13.latest.cohort": "text", "m13.latest.customers": "count",
            "m13.latest.m1_net": "currency", "m13.latest.first90_net": "currency",
            "m13.latest.multiple": "ratio",
        }, section="m13_quality")
        # sources (pendable)
        + _m({
            "r12.sources.top_channel": "text", "r12.sources.top_share": "pct",
            "r12.sources.untracked_share": "pct",
        }, section="sources")
        # spending wisely
        + _m({
            "ytd26.spend": "currency",
            "ytd26.roas_m1": "ratio", "ytd26.roas_to_date": "ratio",
            "ytd26.roas_maturity": "text", "ytd26.repeat_share": "pct",
            "fy26.target": "currency", "ytd26.m1_net": "currency",
            "fy26.required_monthly": "currency", "fy26.forecast_at_run_rate": "currency",
        })
        + _m({"ytd26.spend_share_of_revenue": "pct"}, hib=False)
        # year over year
        + _m({
            "ytd25.m1_net": "currency", "ytd25.new_customers": "count",
            "ytd26.new_customers": "count",
            "ytd26.return_per_dollar": "ratio", "ytd25.return_per_dollar": "ratio",
        })
        + _m({"ytd25.spend": "currency"}, hib=False)
    ),
    claims=(
        "aug26.volume_story", "ytd26.roas_story", "fy26.pace_story",
        "r12.sources_story", "fy26.on_track",
    ),
    charts=("new_customers_12m", "m1_net_12m", "sources_customers"),
    tables=("budget_vs_actual", "online"),
    pendable_sections=("m13_quality", "sources", "online"),
)
