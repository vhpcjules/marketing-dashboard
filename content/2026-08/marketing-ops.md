---
period: 2026-08
dashboard: marketing-ops
prepared: 2026-09-05
audience: "Jules; Jon for technical content review. Everything operational."
claims:
  agency_fee_under_billed_not_over:
    expr: "truad.agency_fee_due_jj26 - spend.agency_billed_jj26"
    assert: "positive"
    render: "${:,.2f}"
    note: "At 0.20 × actual media, Jan–Jul fees due exceeded fees billed, so the August credit is not a 2026 fee overcharge."
  truad_media_reconciles:
    expr: "abs(delta(truad.media_jj26, spend.media_jj26_gl))"
    assert: "between(0, 2)"
    render: "{:.2f}%"
not_carried_forward:
  - "v1 Social: the 'May quick read' callout that sat under a July default view. Month-specific prose is now bound to the month it describes."
  - "v1 Social: the LinkedIn table left at pre-restatement values under a banner saying it had been corrected. There is now one series."
  - "v1 Social: 'Actions (all events)' rendering 0 for July as a −100% finding. A NULL-fed series is refused, not rendered."
  - "v1 Marketing Activity: '$3,310 across 6 ad sets' alongside '$1,299 across two'. The campaign figure comes from one source."
  - "v1 Marketing Activity: 'the net loss of 112 is more than the giveaway gained (155)'. It is not."
---

# Marketing operations — August 2026

## Channel performance

{{ m("channels.summary_note") }}

### Paid media — what the agency spent, reconciled to the ledger

The agency platform reports media spend by channel; the general ledger
records what we paid. For January–July the two agree within
{{ c("truad_media_reconciles") }} — {{ m("truad.media_jj26") }} on the
platform against {{ m("spend.media_jj26_gl") }} in the ledger — so the media
figures below are trustworthy. Google is Paid Search plus Performance Max;
Meta is Paid Social.

The one month that does not agree is April: ledger media is
{{ m("truad.april_gap") }} below the platform and no agency fee posted at all.
Every other month is within a few hundred dollars. April looks like missing
or late postings and is worth a query to accounting.

**Do not use the agency platform's revenue or ROAS columns anywhere.** For
January–August it reports {{ m("truad.platform_revenue_ytd") }} of
"platform revenue" on {{ m("truad.media_ytd") }} of media, an implied
{{ m("truad.platform_roas") }} per dollar. NetSuite says the customers
acquired this year have produced {{ m("ytd26.cohort_revenue_to_date") }} to
date against {{ m("ytd26.spend_true") }} of all marketing spend:
{{ m("ytd26.roas_to_date") }} per dollar. The platform overstates revenue
{{ m("truad.revenue_overstatement_x") }} and return roughly
{{ m("truad.roas_overstatement_x") }}. Its ROAS chart shows monthly values
between {{ m("truad.chart_roas_min") }} and {{ m("truad.chart_roas_max") }}
per dollar. If leadership has seen those, a real return of
{{ m("ytd26.roas_to_date") }} will read as disappointing when it is in fact
ahead of last year.

### Meta Ads

{{ m("meta.summary_note") }}

Campaign objective governs how a campaign is judged. Only campaigns running
the leads objective carry a cost per lead; awareness and traffic campaigns are
top of funnel and are never judged on a lead metric. The contractor lead-gen
campaign is the only leads-objective campaign in the account.

### Instagram and LinkedIn (organic)

{{ m("social.summary_note") }}

LinkedIn impressions and engagement come from page statistics, which break
down by date. Share impressions do not and are not shown as a monthly series.
Any month whose data does not cover the full calendar month is refused rather
than shown — a partial pull understated five months of LinkedIn impressions
by half in the previous build.

### Google Ads, organic search, website

{{ m("google.summary_note") }}

Google Ads conversion value is platform-reported and is labelled as such
wherever it appears; it is not NetSuite revenue.

## Active initiatives

{{ m("initiatives.summary_note") }}

The initiatives table carries the last known status from the previous build
where August data has not yet been pulled, and says so on each row. Nothing
is presented as current that is not.

## Spend detail

### Budget versus actual, by ledger account

{{ m("budget26.table_note") }}

Every dollar of actual spend appears in this table. Accounts with postings but
no approved budget line — the pre-split Advertising account, Events, the
GarageExperts SEO misbooking, the cancelled attribution-software subscription,
promotional products — are shown as explicit rows rather than dropped. The
previous build listed six of ten rows and under-reported actual spend by
{{ m("v1.budget_table_gap") }}.

Three lines to read with care:

- **Advertising** carries {{ m("spend.advertising_jj26") }} of January–March
  postings against no budget. It is Google spend booked before the account
  split: the Google account is empty for those months, the platform shows
  real Google spend in all three, and March's posting equals March's Google
  budget to the dollar. Combined, Google ran
  {{ d("spend.google_combined_jj26", "budget26.google_jj26") }} against plan
  — on plan, not the fifty-eight percent under the previous build reported.
- **Agency fees** are billed as a flat monthly amount that follows the
  budgeted media plan, not twenty percent of actual media. Against actual
  media the agency was under-billed by
  {{ c("agency_fee_under_billed_not_over") }} for January–July. The
  {{ m("corr26.agency_credit") }} August credit is therefore not a 2026 fee
  correction; the agency says it relates to 2025 and has no detail. We are
  trying to get detail. Until then it stays on the ledger, out of every
  monthly figure, and 2025 is not adjusted either.
- **Trade shows**: World of Concrete was cancelled in August, releasing
  {{ m("budget26.woc_released") }} across August, November and December.
  Those months show as released, not underspent — a line the business chose
  not to execute is a different fact from a line it failed to execute.

### Year on year by channel

{{ m("spend.yoy_channel_note") }}

## Cohort and retention detail

### First-90-days quality

{{ m("cohorts.m13_note") }}

Only cohorts whose window has closed are shown. The label is "first 90 days"
because that is what the arithmetic is; it is not three calendar months, and
at cohort edges the difference is real.

### The 2026 cohorts, by age

{{ m("cohorts.maturity_table_note") }}

The revenue-to-date multiple of first-month revenue climbs steeply with age:
about {{ m("cohorts.multiple_1mo") }} at one month,
{{ m("cohorts.multiple_8mo") }} at eight, and {{ m("fy25.m13_multiple_full") }}
for the 2025 class at fourteen. Comparing a young cohort's multiple to an old
one's is meaningless, which is why every aggregate carries its customer-
weighted average age.

### Reorder behaviour

{{ m("retention.summary_note") }}

### Lapsed accounts

{{ m("lapsed.detail_note") }}

Named accounts appear on this page because the site is behind Cloudflare
Access. They never appear on the Executive page.

## Budget asks

| Ask | Price | Basis | Success measure |
|---|---|---|---|
| Google new-customer retargeting, Sep–Dec | {{ m("ask26.retargeting_all_in") }} all-in ({{ m("ask26.retargeting_monthly_media") }}/month media + 20% agency) | 90-day follow from first purchase; 83% of eventual second orders happen inside 90 days | Median days to second order below {{ m("retention.median_days_to_second_order") }}; reorder rate on first orders under $400 above {{ m("retention.reorder_rate_under_400") }} |
| Second customer focus group | {{ m("ask26.focus_group") }} one-time | The only instrument that answers *why* two-thirds of a cohort never reorders | A written set of reorder blockers ranked by frequency, delivered before the Q4 planning cycle |

Both asks total {{ m("ask26.total") }}, inside the {{ m("budget26.woc_released") }}
released by the trade-show cancellation. They are a reallocation within the
approved plan.

If leadership holds the 19% target, a third line belongs here: roughly
{{ m("target26.shortfall_after_available") }} to
{{ m("target26.shortfall_after_available_conservative") }} of incremental
paid media in September, depending on the marginal return assumed.

## Data-quality notes

{{ m("dq.notes") }}

- Five previously published cohort months moved on re-pull, all downward, the
  largest by {{ m("drift.max_move_pct") }}. Published values are held frozen;
  the moves are logged in the restatement report. GarageExperts
  reclassification was tested as a cause and ruled out. Likely late credit
  memos; unconfirmed.
- The August agency credit of {{ m("corr26.agency_credit") }} is awaiting
  detail from the agency.
- April 2026 ledger postings look incomplete (see paid media above).
- The approved budget is not loaded into NetSuite; the budget-versus-actual
  report returns zero for every account. The workbook is the only budget
  source, and the build reads a committed copy of it.
- Two source documents disagree about which customer category id is
  GarageExperts and which is Vendor. Both are excluded, so revenue is
  unaffected; the ids are resolved by name at ingest and asserted.
