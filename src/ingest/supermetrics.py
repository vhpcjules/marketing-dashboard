"""Supermetrics adapter: LinkedIn, Instagram and Meta Ads via the async query flow.

THE TOOLS. In a Claude Code session the flow is two calls:

    mcp__Supermetrics_Marketing_Analytics__data_query               (submit)
    mcp__Supermetrics_Marketing_Analytics__get_async_query_results  (poll until ready)

`QuerySpec.tool_args()` is the argument dict for the first call; the rows
that come back from the second are handed to the adapter through the
executor. Python never calls either tool. The executor contract is

    executor(spec: QuerySpec) -> QueryResult(rows, start_date, end_date)

where start_date/end_date are the range the RESULT covers (from the result
metadata, or the request if the API does not echo it). The adapter checks
that range against the month, and it checks the dates in the rows too.

WHY THE COVERAGE GUARD IS A REFUSAL. v1 pulled LinkedIn on a range that
ended mid-month for five consecutive months, and every one of those months
was published as final. Impressions were understated by about 2x. As a
process instruction ("never treat a partial month as final") it lasted five
months; as a raise it lasts. `pull_*` will not return a body for a month
that is not over, whose result range is not exactly the month, or whose
dated rows do not reach both ends of it.

THE FIELD GUARDS, each a real defect:

  LinkedIn    page_impressions / page_engagements / page_engagement_rate
              come from PageStatistics and break down by date.
              total_share_impressions comes from share_statistics and
              CANNOT be broken down by date. They are pulled in separate
              queries, stored under separate keys, and never combined into
              one figure. Asking for both in one query, or for share
              impressions with a date dimension, raises.
  Meta leads  are `onsite_conversion.lead_grouped`. The pixel field
              `offsite_conversions_fb_pixel_lead` is entirely NULL for this
              account; a query naming it raises so it cannot come back as a
              column of zeros that reads like "no leads".
  Meta scope  `campaignobjective` is ALWAYS pulled. Campaigns run
              OUTCOME_LEADS, OUTCOME_SALES and OUTCOME_TRAFFIC; the lead
              metric is computed over OUTCOME_LEADS campaigns only. Judging a
              traffic campaign on leads it was never asked to produce is not
              a finding, it is a category error.

Account ids are constants here because they are facts about the company,
not configuration: there is one LinkedIn page, one Instagram business
account, one Meta ad account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..freeze import SnapshotStore, query_hash
from ..periods import month_end, month_start
from .common import IngestError, Pull, dec, jsonable

__all__ = [
    "DATA_QUERY_TOOL", "RESULTS_TOOL", "Source", "LINKEDIN", "INSTAGRAM", "META_ADS", "GOOGLE_ADS", "GA4", "SOURCES",
    "GOOGLE_ADS_FIELDS", "GA4_FIELDS", "google_ads_spec", "ga4_spec",
    "LINKEDIN_PAGE_FIELDS", "LINKEDIN_SHARE_FIELDS", "INSTAGRAM_FIELDS", "META_FIELDS",
    "META_LEAD_FIELD", "META_FORBIDDEN_FIELDS", "LEAD_OBJECTIVES",
    "QuerySpec", "QueryResult", "Executor", "SupermetricsError", "CoverageError", "FieldGuardError",
    "guard_fields", "assert_full_month_coverage", "linkedin_page_spec", "linkedin_share_spec",
    "instagram_spec", "meta_ads_spec", "SupermetricsAdapter", "ingest_supermetrics",
]

DATA_QUERY_TOOL = "mcp__Supermetrics_Marketing_Analytics__data_query"
RESULTS_TOOL = "mcp__Supermetrics_Marketing_Analytics__get_async_query_results"
SOURCE = "Supermetrics MCP (data_query -> get_async_query_results)"


@dataclass(frozen=True)
class Source:
    ds_id: str
    account: str
    name: str
    domain: str          # snapshot domain
    sparse_days: bool = False   # ad platforms omit days with no delivery; rows need not reach both month edges


LINKEDIN = Source("LIP", "6735901", "LinkedIn Pages", "linkedin")
INSTAGRAM = Source("IGI", "17841402384139665", "Instagram Insights", "instagram")
META_ADS = Source("FA", "act_1162719948574137", "Meta Ads", "meta_ads", sparse_days=True)
GOOGLE_ADS = Source("AW", "4298690564", "Google Ads", "google_ads", sparse_days=True)
GA4 = Source("GAWA", "361664535", "Google Analytics 4 (versatile.net)", "ga4")
SOURCES: Mapping[str, Source] = {s.domain: s for s in (LINKEDIN, INSTAGRAM, META_ADS, GOOGLE_ADS, GA4)}

# Google Ads: field ids are capitalised in this connector (field_discovery
# 2026-09-05). Conversions and ConversionValue are PLATFORM-REPORTED and are
# stored under names that say so; they are never NetSuite revenue. Report
# type Campaign; one row per campaign per day.
GOOGLE_ADS_FIELDS = ("Date", "Campaignname", "AdvertisingChannelType", "Cost", "Impressions", "Clicks",
                     "Conversions", "ConversionValue")
# GA4 (versatile.net property). engagementRate is non-aggregatable at the
# source, so it is recomputed as engagedSessions / sessions over the month.
# 'conversions' is GA4's count of key events; it is labelled as such.
GA4_FIELDS = ("date", "sessions", "engagedSessions", "newUsers", "conversions")

# LinkedIn: two metric families that must never meet in one query.
LINKEDIN_PAGE_FIELDS = ("date", "page_impressions", "page_engagements", "page_engagement_rate")
LINKEDIN_SHARE_FIELDS = ("total_share_impressions",)          # share_statistics; no date breakdown
_LINKEDIN_PAGE_METRICS = frozenset(LINKEDIN_PAGE_FIELDS) - {"date"}

# Instagram Insights. Engagement is stored as its components so the
# component-list check (likes + comments + saves + shares = engagements)
# can hold on the page. Confirm ids with field_discovery(ds_id="IGI") if the
# API rejects a name; do not guess a substitute.
INSTAGRAM_FIELDS = ("date", "reach", "impressions", "profile_views", "follower_count",
                    "likes", "comments", "saved", "shares")

# Meta Ads.
META_LEAD_FIELD = "onsite_conversion.lead_grouped"
META_FORBIDDEN_FIELDS = frozenset({"offsite_conversions_fb_pixel_lead"})
META_FIELDS = ("date", "campaignname", "campaignobjective", "cost", "impressions", "clicks", META_LEAD_FIELD)
LEAD_OBJECTIVES = frozenset({"OUTCOME_LEADS"})
NON_LEAD_OBJECTIVES = frozenset({"OUTCOME_SALES", "OUTCOME_TRAFFIC", "OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT"})


class SupermetricsError(IngestError):
    pass


class CoverageError(SupermetricsError):
    """The result does not cover the whole month. Nothing is written."""


class FieldGuardError(SupermetricsError):
    """A query mixes or names fields the methodology forbids."""


@dataclass(frozen=True)
class QuerySpec:
    source: Source
    fields: tuple[str, ...]
    start_date: date
    end_date: date
    settings: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""

    def __post_init__(self) -> None:
        guard_fields(self.source, self.fields)
        if self.end_date < self.start_date:
            raise ValueError("end_date before start_date")

    @property
    def dated(self) -> bool:
        return any(f.lower() == "date" for f in self.fields)

    def tool_args(self) -> dict[str, Any]:
        """Arguments for the data_query tool. Everything is explicit: no
        relative range ('last_30_days') can ever produce a partial month."""
        return {
            "ds_id": self.source.ds_id,
            "ds_accounts": [self.source.account],
            "fields": list(self.fields),
            "date_range_type": "custom",
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "settings": dict(self.settings),
        }

    def fingerprint(self) -> str:
        """query_hash of what the query IS, not when it ran (dates excluded)."""
        text = "|".join([self.source.ds_id, self.source.account, ",".join(self.fields),
                         ",".join(f"{k}={self.settings[k]}" for k in sorted(self.settings))])
        return query_hash(text)


@dataclass(frozen=True)
class QueryResult:
    rows: tuple[dict, ...]
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(dict(r) for r in self.rows))


Executor = Callable[[QuerySpec], QueryResult]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def guard_fields(source: Source, fields: Sequence[str]) -> None:
    fs = set(fields)
    if source.ds_id == LINKEDIN.ds_id:
        share = fs & set(LINKEDIN_SHARE_FIELDS)
        page = fs & _LINKEDIN_PAGE_METRICS
        if share and page:
            raise FieldGuardError(
                "LinkedIn: page_* metrics (PageStatistics) and total_share_impressions "
                "(share_statistics) must never be pulled in one query; they are different "
                "populations and only one of them breaks down by date"
            )
        if share and "date" in fs:
            raise FieldGuardError(
                "LinkedIn: total_share_impressions cannot be broken down by date; drop the "
                "date dimension for the share query"
            )
    if source.ds_id == META_ADS.ds_id:
        bad = fs & META_FORBIDDEN_FIELDS
        if bad:
            raise FieldGuardError(
                f"Meta: {sorted(bad)} is entirely NULL for account {META_ADS.account}; leads are "
                f"{META_LEAD_FIELD!r} and nothing else"
            )
        if META_LEAD_FIELD in fs and "campaignobjective" not in fs:
            raise FieldGuardError(
                "Meta: campaignobjective must be pulled alongside the lead metric so "
                "OUTCOME_SALES / OUTCOME_TRAFFIC campaigns are never judged on leads"
            )


def _row_date(row: Mapping[str, Any]) -> date:
    raw = row.get("date", row.get("Date"))
    if raw is None:
        raise CoverageError("a dated query returned a row without a 'date' field")
    return raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])


def assert_full_month_coverage(result: QueryResult, month: str, *, as_of: date, dated: bool,
                               sparse_days: bool = False) -> int:
    """Refuse anything short of the whole month. Returns the days covered.

    Three tests, any of which refuses:
      - the month is not over as of `as_of`;
      - the result's own range is not exactly [first day, last day];
      - for dated data, the rows do not reach both the first and the last day.
        Ad platforms (sparse_days) omit days with no delivery, so for them
        the third test is that every row falls inside the month; the range
        test above is what catches the v1 mid-month pull.
    """
    first, last = month_start(month), month_end(month)
    if last >= as_of:
        raise CoverageError(
            f"{month} is not over as of {as_of}: a partial month is never final "
            f"(v1 published five of them and understated LinkedIn impressions 2x)"
        )
    if (result.start_date, result.end_date) != (first, last):
        raise CoverageError(
            f"result covers {result.start_date}..{result.end_date}, not {first}..{last}; "
            f"refusing to write {month} from a range that is not exactly the month"
        )
    if not dated:
        return (last - first).days + 1
    if not result.rows:
        raise CoverageError(f"{month}: no rows at all; an empty month is not a zero month")
    days = {_row_date(r) for r in result.rows}
    if sparse_days:
        if min(days) < first or max(days) > last:
            raise CoverageError(f"{month}: rows span {min(days)}..{max(days)}, outside {first}..{last}")
        return len(days)
    if min(days) != first or max(days) != last:
        raise CoverageError(
            f"{month}: rows span {min(days)}..{max(days)}, not {first}..{last}; "
            f"the source has not finished the month - pull again later, do not write"
        )
    return len(days)


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

def _range(month: str) -> tuple[date, date]:
    return month_start(month), month_end(month)


def linkedin_page_spec(month: str) -> QuerySpec:
    a, b = _range(month)
    return QuerySpec(LINKEDIN, LINKEDIN_PAGE_FIELDS, a, b, {"report_type": "page_statistics"}, "linkedin_page")


def linkedin_share_spec(month: str) -> QuerySpec:
    a, b = _range(month)
    return QuerySpec(LINKEDIN, LINKEDIN_SHARE_FIELDS, a, b, {"report_type": "share_statistics"}, "linkedin_share")


def instagram_spec(month: str) -> QuerySpec:
    a, b = _range(month)
    return QuerySpec(INSTAGRAM, INSTAGRAM_FIELDS, a, b, {}, "instagram")


def meta_ads_spec(month: str) -> QuerySpec:
    a, b = _range(month)
    return QuerySpec(META_ADS, META_FIELDS, a, b, {}, "meta_ads")


def google_ads_spec(month: str) -> QuerySpec:
    a, b = _range(month)
    return QuerySpec(GOOGLE_ADS, GOOGLE_ADS_FIELDS, a, b, {"report_type": "Campaign"}, "google_ads")


def ga4_spec(month: str) -> QuerySpec:
    a, b = _range(month)
    return QuerySpec(GA4, GA4_FIELDS, a, b, {}, "ga4")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

def _sum(rows: Iterable[Mapping[str, Any]], key: str) -> Decimal:
    total = Decimal(0)
    for r in rows:
        v = r.get(key)
        if v not in (None, ""):
            total += dec(v, key)
    return total


def _require(rows: Sequence[Mapping[str, Any]], fields: Iterable[str], what: str) -> None:
    if not rows:
        return
    missing = [f for f in fields if f not in rows[0]]
    if missing:
        raise SupermetricsError(
            f"{what}: result rows lack {missing}; run field_discovery for the source and fix the "
            f"field list - do not substitute a different metric"
        )


def _rate(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return None if denominator == 0 else (numerator / denominator * Decimal(100)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class SupermetricsAdapter:
    executor: Executor

    def _run(self, spec: QuerySpec, month: str, as_of: date) -> tuple[QueryResult, int]:
        result = self.executor(spec)
        days = assert_full_month_coverage(result, month, as_of=as_of, dated=spec.dated,
                                          sparse_days=spec.source.sparse_days)
        return result, days

    def pull_linkedin(self, month: str, *, as_of: date) -> Pull:
        page_spec, share_spec = linkedin_page_spec(month), linkedin_share_spec(month)
        page, days = self._run(page_spec, month, as_of)
        share, _ = self._run(share_spec, month, as_of)
        _require(page.rows, LINKEDIN_PAGE_FIELDS, "linkedin page_statistics")
        impressions = _sum(page.rows, "page_impressions")
        engagements = _sum(page.rows, "page_engagements")
        share_rows = list(share.rows)
        if len(share_rows) != 1:
            raise SupermetricsError(f"linkedin share_statistics: expected one undated row, got {len(share_rows)}")
        body = {
            "page_statistics": {
                "page_impressions": impressions,
                "page_engagements": engagements,
                # The rate is recomputed from the two sums rather than averaged
                # from the daily rate column: a mean of daily rates weights a
                # 10-impression Sunday like a 2,000-impression Tuesday.
                "page_engagement_rate_pct": _rate(engagements, impressions),
                "days_covered": days,
                "source_family": "PageStatistics (breaks down by date)",
            },
            "share_statistics": {
                "total_share_impressions": dec(share_rows[0]["total_share_impressions"], "total_share_impressions"),
                "source_family": "share_statistics (whole-month figure; cannot be broken down by date)",
            },
            "note": ("page_* and total_share_impressions are different populations and are never "
                     "summed, compared, or divided into one another"),
        }
        return Pull(jsonable(body), len(page.rows) + len(share_rows),
                    query_hash(page_spec.fingerprint() + "+" + share_spec.fingerprint()))

    def pull_instagram(self, month: str, *, as_of: date) -> Pull:
        spec = instagram_spec(month)
        result, days = self._run(spec, month, as_of)
        _require(result.rows, INSTAGRAM_FIELDS, "instagram")
        by_date = sorted(result.rows, key=_row_date)
        likes, comments, saves, shares = (_sum(by_date, k) for k in ("likes", "comments", "saved", "shares"))
        body = {
            "reach": _sum(by_date, "reach"),
            "impressions": _sum(by_date, "impressions"),
            "profile_views": _sum(by_date, "profile_views"),
            # follower_count is a level, not a flow: take the last day's value.
            "followers_end_of_month": dec(by_date[-1]["follower_count"], "follower_count"),
            "engagement_components": {"likes": likes, "comments": comments, "saves": saves, "shares": shares},
            "engagements": likes + comments + saves + shares,
            "days_covered": days,
        }
        return Pull(jsonable(body), len(result.rows), spec.fingerprint())

    def pull_meta_ads(self, month: str, *, as_of: date) -> Pull:
        spec = meta_ads_spec(month)
        result, days = self._run(spec, month, as_of)
        _require(result.rows, META_FIELDS, "meta_ads")
        campaigns: dict[tuple[str, str], dict[str, Decimal]] = {}
        for r in result.rows:
            key = (str(r["campaignname"]), str(r["campaignobjective"]).upper())
            c = campaigns.setdefault(key, {"spend": Decimal(0), "impressions": Decimal(0),
                                           "clicks": Decimal(0), "leads": Decimal(0)})
            c["spend"] += dec(r.get("cost") or 0, "cost")
            c["impressions"] += dec(r.get("impressions") or 0, "impressions")
            c["clicks"] += dec(r.get("clicks") or 0, "clicks")
            c["leads"] += dec(r.get(META_LEAD_FIELD) or 0, META_LEAD_FIELD)

        rows_out, lead_spend, leads = [], Decimal(0), Decimal(0)
        for (name, objective), c in sorted(campaigns.items()):
            judged_on_leads = objective in LEAD_OBJECTIVES
            if judged_on_leads:
                lead_spend += c["spend"]
                leads += c["leads"]
            rows_out.append({
                "campaign": name, "objective": objective,
                "spend": c["spend"], "impressions": c["impressions"], "clicks": c["clicks"],
                # None, not 0: a traffic campaign has no lead figure to show.
                "leads": c["leads"] if judged_on_leads else None,
                "judged_on_leads": judged_on_leads,
            })
        spend = sum((c["spend"] for c in campaigns.values()), Decimal(0))
        impressions = sum((c["impressions"] for c in campaigns.values()), Decimal(0))
        clicks = sum((c["clicks"] for c in campaigns.values()), Decimal(0))
        body = {
            "spend": spend, "impressions": impressions, "clicks": clicks,
            "cpm": None if impressions == 0 else (spend / impressions * Decimal(1000)).quantize(Decimal("0.01")),
            "ctr_pct": _rate(clicks, impressions),
            "lead_campaigns": {
                "objectives_counted": sorted(LEAD_OBJECTIVES),
                "spend": lead_spend, "leads": leads,
                "cost_per_lead": None if leads == 0 else (lead_spend / leads).quantize(Decimal("0.01")),
                "lead_field": META_LEAD_FIELD,
            },
            "objectives_present": sorted({o for _, o in campaigns}),
            "campaigns": rows_out,
            "days_covered": days,
            "note": ("leads and cost per lead are computed over OUTCOME_LEADS campaigns only; "
                     "OUTCOME_SALES / OUTCOME_TRAFFIC campaigns carry leads=null by design"),
        }
        return Pull(jsonable(body), len(result.rows), spec.fingerprint())

    def pull_google_ads(self, month: str, *, as_of: date) -> Pull:
        spec = google_ads_spec(month)
        result, days = self._run(spec, month, as_of)
        _require(result.rows, GOOGLE_ADS_FIELDS, "google_ads")
        camps: dict[tuple[str, str], dict[str, Decimal]] = {}
        for r in result.rows:
            key = (str(r["Campaignname"]), str(r["AdvertisingChannelType"]))
            c = camps.setdefault(key, {k: Decimal(0) for k in ("cost", "impressions", "clicks", "conversions", "value")})
            c["cost"] += dec(r.get("Cost") or 0, "Cost")
            c["impressions"] += dec(r.get("Impressions") or 0, "Impressions")
            c["clicks"] += dec(r.get("Clicks") or 0, "Clicks")
            c["conversions"] += dec(r.get("Conversions") or 0, "Conversions")
            c["value"] += dec(r.get("ConversionValue") or 0, "ConversionValue")
        rows_out = [{"campaign": n, "channel_type": t, "cost": c["cost"], "impressions": c["impressions"],
                     "clicks": c["clicks"], "platform_conversions": c["conversions"],
                     "platform_conversion_value": c["value"]} for (n, t), c in sorted(camps.items())]
        cost = sum((c["cost"] for c in camps.values()), Decimal(0))
        impressions = sum((c["impressions"] for c in camps.values()), Decimal(0))
        clicks = sum((c["clicks"] for c in camps.values()), Decimal(0))
        by_type: dict[str, Decimal] = {}
        for (_, t), c in camps.items():
            by_type[t] = by_type.get(t, Decimal(0)) + c["cost"]
        body = {
            "cost": cost, "impressions": impressions, "clicks": clicks,
            "ctr_pct": _rate(clicks, impressions),
            "avg_cpc": None if clicks == 0 else (cost / clicks).quantize(Decimal("0.01")),
            "cost_by_channel_type": by_type,
            "platform_conversions": sum((c["conversions"] for c in camps.values()), Decimal(0)),
            "platform_conversion_value": sum((c["value"] for c in camps.values()), Decimal(0)),
            "campaigns": rows_out,
            "days_covered": days,
            "note": ("platform_conversions and platform_conversion_value are Google Ads' own attribution and "
                     "are labelled platform-reported wherever shown; they are never NetSuite revenue"),
        }
        return Pull(jsonable(body), len(result.rows), spec.fingerprint())

    def pull_ga4(self, month: str, *, as_of: date) -> Pull:
        spec = ga4_spec(month)
        result, days = self._run(spec, month, as_of)
        _require(result.rows, GA4_FIELDS, "ga4")
        sessions = _sum(result.rows, "sessions")
        engaged = _sum(result.rows, "engagedSessions")
        body = {
            "sessions": sessions,
            "engaged_sessions": engaged,
            "engagement_rate_pct": _rate(engaged, sessions),
            "new_users": _sum(result.rows, "newUsers"),
            "key_events": _sum(result.rows, "conversions"),
            "days_covered": days,
            "property": GA4.account,
            "note": ("engagement rate is engaged sessions over sessions for the month, not a mean of daily rates; "
                     "key_events is GA4's 'conversions' count of configured key events, not orders"),
        }
        return Pull(jsonable(body), len(result.rows), spec.fingerprint())

    def pull(self, domain: str, month: str, *, as_of: date) -> Pull:
        fn = {"linkedin": self.pull_linkedin, "instagram": self.pull_instagram, "meta_ads": self.pull_meta_ads,
              "google_ads": self.pull_google_ads, "ga4": self.pull_ga4}
        if domain not in fn:
            raise SupermetricsError(f"unknown Supermetrics domain {domain!r}; choose from {sorted(fn)}")
        return fn[domain](month, as_of=as_of)


def specs_for(domain: str, month: str) -> list[QuerySpec]:
    """The data_query calls a domain needs for a month, in execution order."""
    if domain == "linkedin":
        return [linkedin_page_spec(month), linkedin_share_spec(month)]
    if domain == "instagram":
        return [instagram_spec(month)]
    if domain == "meta_ads":
        return [meta_ads_spec(month)]
    if domain == "google_ads":
        return [google_ads_spec(month)]
    if domain == "ga4":
        return [ga4_spec(month)]
    raise SupermetricsError(f"unknown Supermetrics domain {domain!r}")


def ingest_supermetrics(adapter: SupermetricsAdapter, store: SnapshotStore, domain: str, month: str, *,
                        as_of: date, pulled_at=None):
    """Pull and write one social/ads domain for one month. Refuses partial
    months (CoverageError) and frozen months (FreezeError, from the store)."""
    from datetime import datetime, timezone
    pull = adapter.pull(domain, month, as_of=as_of)
    return store.write_open(month, domain, pull.body, query_id=f"supermetrics:{domain}",
                            query_hash_=pull.query_hash, row_count=pull.row_count,
                            pulled_at=pulled_at or datetime.now(timezone.utc).replace(microsecond=0),
                            source=f"{SOURCE}; {SOURCES[domain].name} {SOURCES[domain].account}")
