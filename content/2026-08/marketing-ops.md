---
period: 2026-08
dashboard: marketing-ops
prepared: 2026-09-05
audience: "Jules; Jon for technical content review. Everything operational."
claims:
  media_reconciles:
    expr: "abs(delta(spend.media_gl_closed, truad.media_closed))"
    assert: "between(0, 2)"
    render: "{:.2f}%"
    note: "Ledger media against platform media over the closed months. Above two percent the media figures below could not be trusted."
  agency_fee_under_billed_not_over:
    expr: "recon.fee_under_billed_closed"
    assert: "positive"
    render: "${:,.2f}"
    note: "At the approved rate on actual media, fees due exceeded fees billed, so the August credit is not a 2026 fee overcharge."
  google_vs_plan:
    expr: "delta(ytd26.google_combined_actual, ytd26.google_budget)"
    assert: "between(-10, 10)"
    render: "{:+.0f}%"
    note: "Google account plus the pre-split Advertising account, against Google's own approved line. Inside ten percent either way is 'on plan'."
not_carried_forward:
  - "v1 Social: the 'May quick read' callout that sat under a July default view. Month-specific prose is now bound to the month it describes."
  - "v1 Social: the LinkedIn table left at pre-restatement values under a banner saying it had been corrected. There is now one series."
  - "v1 Social: 'Actions (all events)' rendering 0 for July as a −100% finding. A NULL-fed series is refused, not rendered."
  - "v1 Marketing Activity: '$3,310 across 6 ad sets' alongside '$1,299 across two'. The campaign figure will come from one source when Meta Ads is ingested."
  - "v1 Marketing Activity: 'the net loss of 112 is more than the giveaway gained (155)'. It is not."
  - "v1 Marketing Activity: the September asks priced at $3,000 and $1,750 without the agency surcharge. Re-priced all-in below."
---

# Marketing operations — August 2026

## Channel performance

### Paid media — what the agency spent, reconciled to the ledger

The agency platform reports media spend by channel; the general ledger
records what we paid. Over the closed months the two agree within
{{ c("media_reconciles") }} — {{ m("truad.media_closed") }} on the platform
against {{ m("spend.media_gl_closed") }} in the ledger — so the media figures
above are trustworthy. Google is Paid Search plus Performance Max; Meta is
Paid Social.

The month that disagrees most is {{ m("recon.worst_gap_month") }}: ledger
media minus platform media is {{ m("recon.worst_gap") }}, and no agency fee
posted at all that month. Every other month is within a few hundred dollars.
It looks like missing or late postings and is worth a query to accounting.

**Do not use the agency platform's revenue or return columns anywhere.** For
January–August the platform reports {{ m("truad.platform_revenue_ytd") }} of
"platform revenue" on {{ m("truad.media_ytd") }} of media, an implied
{{ m("truad.platform_roas") }} per dollar. NetSuite says the customers
acquired this year have produced {{ m("ytd26.revenue_to_date") }} to date
against {{ m("ytd26.spend") }} of all marketing spend:
{{ m("ytd26.roas_to_date") }} per dollar. The platform overstates revenue
{{ m("truad.revenue_overstatement") }} and return roughly
{{ m("truad.roas_overstatement") }}. Its own chart shows monthly values
between {{ m("truad.platform_roas_min") }} and
{{ m("truad.platform_roas_max") }} per dollar. If leadership has seen those,
a real return of {{ m("ytd26.roas_to_date") }} will read as disappointing
when it is in fact ahead of last year.

### Meta Ads

August Meta spend was {{ m("aug26.meta.spend") }} on the platform, which
matches the agency's Paid Social line to the cent and sits inside the ledger
reconciliation above. The account ran {{ m("aug26.meta.impressions") }}
impressions and {{ m("aug26.meta.clicks") }} clicks, a click-through rate of
{{ m("aug26.meta.ctr") }} at {{ m("aug26.meta.cpm") }} per thousand
impressions. Campaign objective governs how a campaign is judged: only the
two leads-objective ad sets carry a lead figure — {{ m("aug26.meta.leads") }}
leads on {{ m("aug26.meta.lead_spend") }}, {{ m("aug26.meta.cost_per_lead") }}
each — and the awareness, traffic and sales ad sets are never judged on leads
they were not asked to produce. The new August contractor list ad set is the
larger of the two lead campaigns; one month is not a verdict on it.

### LinkedIn, Instagram, Google and the website

LinkedIn page impressions were {{ m("aug26.li.impressions") }} in August with
{{ m("aug26.li.engagements") }} engagements, an engagement rate of
{{ m("aug26.li.engagement_rate") }} computed as engagements over impressions
for the month rather than a mean of daily rates. Page statistics break down by
date and are shown as a monthly series; share impressions do not — the share
figure the platform returns is a lifetime total that ignores the date range,
so it is stored and never shown as a monthly number. Any month whose data does
not cover the full calendar month is refused rather than shown; a partial pull
understated five months of LinkedIn impressions by half in the previous build.

Google Ads spent {{ m("aug26.aw.cost") }} on the platform in August, which
matches the agency's Paid Search plus Performance Max lines and the ledger
reconciliation above, for {{ m("aug26.aw.clicks") }} clicks at
{{ m("aug26.aw.avg_cpc") }} each. The platform attributes
{{ m("aug26.aw.platform_conversion_value") }} of conversion value to those
clicks; that is Google's own attribution, shown so the gap is visible, and it
is never treated as NetSuite revenue. The website (versatile.net) took
{{ m("aug26.ga.sessions") }} sessions in August with an engagement rate of
{{ m("aug26.ga.engagement_rate") }}; key events are {{ term("ga4") }} configured events,
not orders.

Instagram is not shown this month: the Supermetrics connection to Instagram
Insights has expired and must be renewed before it can be pulled.

## Spend detail

Every dollar of actual spend appears in the budget-versus-actual table.
Accounts with postings but no approved budget line — the pre-split
Advertising account, Events, the GarageExperts SEO misbooking, the cancelled
attribution-software subscription, promotional products — are shown as
explicit rows rather than dropped.

Three lines to read with care:

- **Advertising** carries {{ m("ytd26.actual.66212_0020") }} of January–March
  postings against no budget. It is Google spend booked before the account
  split: the Google account is empty for those months, the platform shows
  real Google spend in all three, and March's posting equals March's Google
  budget to the dollar. Combined, Google has run {{ c("google_vs_plan") }}
  against its own plan — on plan, not the fifty-eight percent under that the
  previous build reported.
- **Agency fees** are billed as a flat monthly amount that follows the
  budgeted media plan, not a fifth of actual media. Against actual media the
  agency was under-billed by {{ c("agency_fee_under_billed_not_over") }} over
  the closed months. The {{ m("corr26.agency_credit_pending_detail") }}
  August credit is therefore not a 2026 fee correction; the agency says it
  relates to last year and has no detail. We are trying to get detail. Until
  then it stays on the ledger, out of every monthly figure, and last year is
  not adjusted either.
- **Trade shows**: World of Concrete was cancelled in August, releasing
  {{ m("budget26.released_by_cancellation") }} across August, November and
  December. Those months show as released, not underspent — a line the
  business chose not to execute is a different fact from a line it failed to
  execute.

## Cohort and retention detail

The revenue-to-date multiple of first-month revenue climbs steeply with age:
about {{ m("aug26.multiple") }} at one month, {{ m("jan26.multiple") }} at
eight, and {{ m("fy25.multiple_to_date") }} for last year's class at
{{ m("fy25.avg_maturity") }}. Comparing a young cohort's multiple to an old
one's is meaningless, which is why every aggregate carries its
customer-weighted average age.

Reorder behaviour is the reason the retargeting ask exists. Of the
{{ m("retention.customers") }} customers with a first order in the window,
those whose first order was small reorder at {{ m("retention.under_400.rate") }}
against {{ m("retention.400_2499.rate") }} for mid-sized first orders — a
{{ d("retention.under_400.rate", "retention.400_2499.rate") }} relative gap.
Among customers who did reorder, the median wait was
{{ m("retention.median_days_to_second_order") }} days and
{{ m("retention.reordered_by_day_90") }} of second orders had arrived by day
ninety. A ninety-day follow window covers most of the behaviour we want to
influence.

Named lapsed accounts appear on this page, never on the Executive page. The
lapsed-accounts query has not been run this month.

## Budget asks

Both asks total {{ m("ask26.total") }}, inside the
{{ m("budget26.released_by_cancellation") }} released by the trade-show
cancellation. They are a reallocation within the approved plan.

Leadership's target is growth in total company NET revenue, not in
new-customer revenue. Pricing what it would take to close the company's
run-rate gap needs the monthly total-revenue series, which has not yet been
pulled into this build; the tiles above say so rather than estimating. Money
already available inside the approved plan — released trade-show budget plus
the year-to-date variance — is {{ m("fy26.available_within_plan") }}.

## Data-quality notes

- Previously published cohort months were re-pulled. {{ c("build.drift_story") }}
  The largest move was {{ m("build.drift_max_move") }}, downward.
  GarageExperts reclassification was tested as a cause and ruled out. Likely
  late credit memos; unconfirmed.
- The August agency credit of {{ m("corr26.agency_credit_pending_detail") }}
  is awaiting detail from the agency.
- The {{ m("recon.worst_gap_month") }} ledger postings look incomplete (see
  paid media above).
- The approved budget is not loaded into NetSuite; the budget-versus-actual
  report there returns zero for every account. The workbook is the only
  budget source, and the build reads a committed copy of it.
- Two source documents disagree about which customer category id is
  GarageExperts and which is Vendor. Both are excluded, so revenue is
  unaffected; the ids are resolved by name at ingest and asserted.
- The legacy-account (vintage) figures on the Executive page are the
  published Sage-basis numbers. NetSuite carries migration dates, not
  acquisition dates, for legacy accounts; a Sage created-date export is
  needed to refresh them.
