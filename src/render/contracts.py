"""What each page needs from the registry, declared once.

A template with StrictUndefined fails on the first missing figure and stops.
That is the right behaviour at render time, but a build wants to know
everything that is missing at once, and the test suite wants to synthesise
a registry that satisfies the page without reading the template. So each
page declares its contract: the metric IDs (with kind), the claim IDs, the
chart keys and the table keys it will ask for.

Contracts are written with period placeholders - `{cur}.new_customers`,
`{ytd}.spend` - and resolved for one reporting month by `for_period()`.
Templates address the same prefixes through the `ids` context mapping
(`m(ids.cur ~ '.new_customers')`), so neither carries a month name and the
September build needs no edit to either.

`MetricRegistry` does not know about contracts. `check()` is a pure
comparison, so a page can be checked before a single template is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from ..populate import period_ids

__all__ = ["MetricSpec", "PageContract", "EXECUTIVE", "MARKETING_OPS", "SALES", "ALL"]


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

    @property
    def slug(self) -> str:
        return self.template.rsplit(".", 1)[0]

    def for_period(self, reporting_month: str) -> "PageContract":
        """Resolve `{cur}`, `{prev}`, `{ytd}`, `{pytd}`, `{fy}`, `{pfy}`, `{yy}`, `{pyy}`."""
        ids = period_ids(reporting_month)
        return replace(
            self,
            metrics=tuple(replace(s, metric_id=s.metric_id.format(**ids)) for s in self.metrics),
            claims=tuple(c.format(**ids) for c in self.claims),
        )

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


# Figures every page's template asks for through the core registration.
_CORE = (
    _m({
        "{cur}.new_customers": "count", "{prev}.new_customers": "count",
        "{cur}.m1_net": "currency", "{prev}.m1_net": "currency",
        "{cur}.avg_first_order": "currency", "{prev}.avg_first_order": "currency",
    })
)

EXECUTIVE = PageContract(
    template="executive.html",
    metrics=tuple(
        _CORE
        + _m({"{cur}.m1_return_per_dollar": "ratio"})
        # first 90 days, latest closed cohort (pendable)
        + _m({
            "m13.latest.cohort": "text", "m13.latest.customers": "count",
            "m13.latest.m1_net": "currency", "m13.latest.first90_net": "currency",
            "m13.latest.multiple": "ratio",
        }, section="m13_quality")
        # sources (pendable)
        + _m({
            "r12.sources.top_channel": "text", "r12.sources.top_share": "pct",
            "r12.sources.customers": "count",
        }, section="sources")
        + _m({"r12.sources.untracked_share": "pct"}, hib=False, section="sources")
        # spending wisely
        + _m({
            "{ytd}.spend": "currency",
            "{ytd}.roas_m1": "ratio", "{ytd}.roas_to_date": "ratio",
            "{ytd}.roas_maturity": "text", "{ytd}.repeat_share": "pct",
            "{fy}.target": "currency", "{fy}.target_growth": "pct", "{ytd}.m1_net": "currency",
        })
        # pace against the total-revenue target (pendable until revenue_total is ingested)
        + _m({
            "{ytd}.total_net": "currency", "{pytd}.total_net": "currency", "{pfy}.total_net": "currency",
            "{fy}.required_monthly": "currency", "{fy}.forecast_at_run_rate": "currency",
            "{fy}.still_needed": "currency", "{ytd}.total_run_rate": "currency",
            "{pfy}.total_remaining_months": "currency",
        }, section="pace")
        + _m({"{fy}.gap_at_run_rate": "currency"}, hib=False, section="pace")
        # what closing the gap would take (pace) and what is available inside the plan (budget)
        + _m({"{fy}.spend_to_close_at_marketing_return": "currency", "{fy}.spend_to_close_conservative": "currency"},
             hib=False, section="pace")
        + _m({"{fy}.available_within_plan": "currency"}, section="budget")
        # the plan
        + _m({"budget{yy}.ytd_effective": "currency", "budget{yy}.annual_approved": "currency",
              "budget{yy}.annual_effective": "currency", "budget{yy}.released_by_cancellation": "currency"},
             section="budget")
        + _m({"{ytd}.cost_per_customer": "currency", "{pytd}.cost_per_customer": "currency"}, hib=False, section="budget")
        # the asks
        + _m({"ask{yy}.total": "currency", "ask{yy}.period": "text", "ask{yy}.agency_rate": "pct"}, section="asks")
        # year over year
        + _m({
            "{pytd}.m1_net": "currency", "{pytd}.new_customers": "count",
            "{ytd}.new_customers": "count",
            "{ytd}.return_per_dollar": "ratio", "{pytd}.return_per_dollar": "ratio",
        })
        + _m({"{pytd}.spend": "currency"}, hib=False)
        # legacy accounts: the template picks the published or the Sage-joined id set by
        # report.vintage_basis and fails loudly on a missing id, so the contract lists neither.
        # The month-by-month scorecard and the twelve-month record are driven by the
        # `scorecard` context list, whose ids the template derives; a missing one fails the render.
    ),
    claims=(
        "{cur}.volume_story", "{ytd}.roas_story", "{fy}.pace_story",
        "r12.sources_story", "{fy}.on_track",
    ),
    charts=("new_customers_12m", "m1_net_12m", "sources_customers", "total_net_yoy"),
    tables=("budget_vs_actual", "online", "record_12m", "asks"),
    pendable_sections=("m13_quality", "sources", "online", "instagram", "vintage", "pace", "budget", "asks"),
)

MARKETING_OPS = PageContract(
    template="marketing-ops.html",
    metrics=tuple(
        _CORE
        + _m({"{cur}.spend_true": "currency", "{cur}.spend_as_posted": "currency", "{ytd}.spend": "currency"}, hib=False)
        + _m({
            "{ytd}.roas_m1": "ratio", "{ytd}.roas_to_date": "ratio", "{ytd}.revenue_to_date": "currency",
            "{ytd}.roas_maturity": "text",
        })
        # paid media reconciled to the ledger (pendable)
        + _m({"truad.media_closed": "currency", "spend.media_gl_closed": "currency",
              "spend.agency_billed_closed": "currency"}, hib=False, section="paid_media")
        + _m({"truad.agency_due_closed": "currency", "recon.fee_under_billed_closed": "currency",
              "recon.worst_gap_month": "text", "recon.worst_gap": "currency",
              "truad.platform_revenue_ytd": "currency", "truad.platform_roas": "ratio",
              "truad.platform_roas_min": "ratio", "truad.platform_roas_max": "ratio"}, section="paid_media")
        + _m({"truad.media_ytd": "currency", "truad.revenue_overstatement": "ratio",
              "truad.roas_overstatement": "ratio"}, hib=False, section="paid_media")
        # Meta Ads and LinkedIn (pendable)
        + _m({"{cur}.meta.impressions": "count", "{cur}.meta.clicks": "count", "{cur}.meta.ctr": "pct"}, section="meta_ads")
        + _m({"{cur}.meta.spend": "currency", "{cur}.meta.cpm": "ratio"}, hib=False, section="meta_ads")
        + _m({"{cur}.li.impressions": "count", "{cur}.li.engagements": "count", "{cur}.li.engagement_rate": "pct"},
             section="social")
        + _m({"{cur}.ig.reach": "count", "{cur}.ig.profile_views": "count", "{cur}.ig.engagements": "count"},
             section="instagram")
        + _m({"{cur}.aw.impressions": "count", "{cur}.aw.clicks": "count", "{cur}.aw.ctr": "pct",
              "{cur}.aw.platform_conversion_value": "currency", "{cur}.ga.sessions": "count",
              "{cur}.ga.engaged_sessions": "count", "{cur}.ga.engagement_rate": "pct", "{cur}.ga.new_users": "count",
              "{cur}.ga.key_events": "count"}, section="google_web")
        + _m({"{cur}.aw.cost": "currency", "{cur}.aw.avg_cpc": "currency"}, hib=False, section="google_web")
        # cohorts by age
        + _m({"{pfy}.multiple_to_date": "ratio", "{pfy}.avg_maturity": "text", "{ytd}.avg_maturity": "text"})
        # retention (pendable)
        + _m({"retention.median_days_to_second_order": "count"}, hib=False, section="retention")
        + _m({"retention.reordered_by_day_30": "pct", "retention.reordered_by_day_90": "pct",
              "retention.reorderers": "count", "retention.customers": "count",
              "retention.under_400.rate": "pct", "retention.400_2499.rate": "pct"}, section="retention")
        # asks (pendable)
        + _m({"ask{yy}.total": "currency", "budget{yy}.released_by_cancellation": "currency",
              "ask{yy}.agency_rate": "pct", "{fy}.available_within_plan": "currency"}, section="asks")
        + _m({"{fy}.shortfall_after_available": "currency", "{fy}.spend_to_close_conservative": "currency",
              "{fy}.spend_to_close_at_marketing_return": "currency",
              "{fy}.shortfall_after_available_conservative": "currency"}, hib=False, section="pace")
    ),
    claims=("{fy}.on_track", "budget{yy}.vs_plan_story"),
    charts=("m13_first90_12", "meta_spend_6m", "li_impressions_6m", "ig_reach_6m", "aw_cost_6m", "ga_sessions_6m"),
    tables=("paid_media_recon", "budget_vs_actual", "yoy_channel", "m13_cohorts", "cohorts_by_age",
            "retention_bands", "asks", "meta_adsets", "aw_channel_types", "record_12m"),
    pendable_sections=("paid_media", "meta_ads", "social", "instagram", "google_web", "initiatives", "yoy_channel",
                       "m13_quality", "retention", "lapsed", "asks", "pace"),
)

SALES = PageContract(
    template="sales.html",
    metrics=tuple(
        _CORE
        # this month's pipeline (pendable)
        + _m({"{cur}.lead_records": "count", "{prev}.lead_records": "count",
              "{cur}.leads_converted": "count", "{prev}.leads_converted": "count",
              "{cur}.lead_conversion": "pct", "{prev}.lead_conversion": "pct",
              "{cur}.leads_assigned": "count", "{cur}.assigned_conversion": "pct",
              "{cur}.unassigned_converted": "count",
              "{cur}.phone_capture": "pct", "{prev}.phone_capture": "pct",
              "{cur}.phone_capture_assigned": "pct", "{cur}.email_capture": "pct",
              "{cur}.with_phone": "count", "{cur}.assigned_with_phone": "count"}, section="pipeline")
        + _m({"{cur}.unassigned_records": "count", "{cur}.unassigned_share": "pct"}, hib=False, section="pipeline")
        # fourteen-month routing
        + _m({"r14.unassigned_conversions": "count", "r14.unassigned_rate": "pct",
              "r14.assigned_converted": "count", "r14.assigned_rate": "pct"})
        + _m({"r14.unassigned_records": "count"}, hib=False)
        # context (pendable)
        + _m({"r12.lead_records": "count", "r12.leads_converted": "count", "r12.lead_conversion": "pct",
              "r12.phone_capture": "pct", "r3.lead_records": "count", "r3.leads_converted": "count",
              "r3.lead_conversion": "pct", "r12.new_customers": "count", "r12.m1_net": "currency"}, section="context")
        # geography (pendable)
        + _m({"geo.total_customers": "count"}, section="geography")
        + _m({"geo.no_state_customers": "count"}, hib=False, section="geography")
    ),
    claims=("{cur}.rep_spread_story", "{cur}.phone_capture_story", "{cur}.unassigned_story"),
    charts=("phone_capture_12m", "lead_records_12m", "geo_customers"),
    tables=("reps", "geography"),
    pendable_sections=("pipeline", "context", "geography"),
)

ALL = (EXECUTIVE, MARKETING_OPS, SALES)
