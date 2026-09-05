---
period: 2026-08
dashboard: executive
prepared: 2026-09-05
claims:
  required_uplift_remaining_months:
    expr: "delta(fy26.still_needed, fy25.m1_remaining_months)"
    assert: "positive"
    render: "{:+.0f}%"
    note: "What the remaining months must deliver, relative to the same months last year. Positive means an acceleration is required."
  forecast_vs_prior_year:
    expr: "delta(fy26.forecast_at_run_rate, fy25.m1_net)"
    assert: "between(-50, 50)"
    render: "{:+.1f}%"
  shortfall_at_m1_return:
    expr: "fy26.shortfall_after_available"
    assert: "nonzero"
    render:
      positive: "leaves roughly ${:,.0f} still to find beyond the approved plan"
      negative: "fits inside the approved plan with roughly ${:,.0f} to spare"
  shortfall_at_cautious_return:
    expr: "fy26.shortfall_after_available_conservative"
    assert: "nonzero"
    render:
      positive: "the ask rises to roughly ${:,.0f} of new money"
      negative: "it still fits, with roughly ${:,.0f} to spare"
  roas_exceeds_prior_year_at_a_third_of_maturity:
    expr: "ytd26.roas_to_date - fy25.roas_to_date"
    assert: "positive"
    render: "${:.2f}"
  both_asks_fit_in_released_budget:
    expr: "budget26.released_by_cancellation - ask26.total"
    assert: "positive"
    render: "${:,.0f}"
  legacy_multiple:
    expr: "vintage.pre2018_avg_annual_net / vintage.band_2025_avg_annual_net"
    assert: "between(9, 13)"
    render: "{:.0f}×"
    note: "v1 published 20×. That was the 2010–2012 band alone against 2025; across all pre-2018 accounts the multiple is about 11×."
not_carried_forward:
  - "v1 Budget: '$33,177 under YTD' and '$56K–$104K of headroom'. The approved budget is $206,346, not $291,545, and the year is not under plan by that amount. Corrected in 'Are we spending wisely?'."
  - "v1 Budget: 'Google 58% under-spent, $65,630 available'. Jan–Mar Google spend sat in the Advertising account before the split; combined, Google is close to plan. Corrected on the Marketing Ops page."
  - "v1 YoY: 'a legacy account is worth 20× a new one'. The all-pre-2018 average is about 11×. Corrected below; still the load-bearing finding."
  - "v1 Leadership: the 8-wedge source donut. Replaced by sorted bars in one colour with the untracked bucket highlighted."
  - "v1 Leadership: 'Are we spending wisely?' channel ROI table with an OVERALL row that did not sum ($260,250 gap). Rebuilt from the data layer with a computed total."
  - "v1 Budget: the '$41,777 under-reported actual' figure quoted in early drafts of this rebuild. It was a transcription error in the fixture file, not a v1 defect, and is withdrawn."
---

# Marketing performance — August 2026

## The three things to take from this month

### Hitting the target now requires a second-half acceleration

Leadership's target is {{ m("fy26.target_growth") }} growth in new-customer
first-month NET revenue over last year. Through August we have booked
{{ m("ytd26.m1_net") }} against a full-year target of {{ m("fy26.target") }}.
The remaining months must deliver {{ m("fy26.still_needed") }}, or
{{ m("fy26.required_monthly") }} every month — {{ c("required_uplift_remaining_months") }}
above the same months last year ({{ m("fy25.m1_remaining_months") }}), after
a first eight months that ran roughly flat. At the recent run rate of
{{ m("ytd26.run_rate") }} a month, the year lands at
{{ m("fy26.forecast_at_run_rate") }}, or {{ c("forecast_vs_prior_year") }}
against last year's {{ m("fy25.m1_net") }}. The gap is
{{ m("fy26.gap_at_run_rate") }}.

That gap is close to closable with money already inside the plan. At the
year-to-date first-month return of {{ m("ytd26.roas_m1") }} per dollar,
closing it needs about {{ m("fy26.spend_to_close_at_m1_roas") }} of
additional spend. Cancelling World of Concrete released
{{ m("budget26.released_by_cancellation") }}, and the year is running
{{ c("budget26.vs_plan_story") }}, so that spend
{{ c("shortfall_at_m1_return") }}. On a more cautious marginal return of two
dollars and fifty cents, {{ c("shortfall_at_cautious_return") }}. Either way
it is a September decision, not an October one: the window is four months and
the lag from record creation to first order eats into it.

### August rebounded; July was a dip, not a trend

August brought {{ m("aug26.new_customers") }} new customers who bought in
their first month, the most of any month this year, and
{{ m("aug26.m1_net") }} of first-month NET revenue at an average first order
of {{ m("aug26.avg_first_order") }}. Last month's {{ m("jul26.avg_first_order") }}
average — flagged then as an early warning on deal size — did not repeat.
One month is one month; August says July was the outlier.

August marketing spend was {{ m("aug26.spend_true") }} on the true-operating
basis. Two cautions on that figure: August is still an open period, and no
agency fee has posted for it yet (the approved plan carries
{{ m("aug26.budget_agency_fee") }} for the month), so expect the final August
figure to be somewhat higher.

### First-month return understates what the spend bought by about half

Judged on first-month revenue alone, every marketing dollar this year has
returned {{ m("ytd26.roas_m1") }}. But the customers acquired this year have
kept buying: their revenue to date is {{ m("ytd26.revenue_to_date") }}, of
which {{ m("ytd26.repeat_share") }} arrived after their first month. On that
basis the return is {{ m("ytd26.roas_to_date") }} per dollar — already
{{ c("roas_exceeds_prior_year_at_a_third_of_maturity") }} ahead of the full
prior-year class's {{ m("fy25.roas_to_date") }}, with this year's cohorts
averaging {{ m("ytd26.avg_maturity") }} of age against
{{ m("fy25.avg_maturity") }}. First-month return closes fast enough to steer
on; revenue to date is the truer measure of what the money bought. Both are
shown, always labelled, and the target is graded on the first.

## Are we growing?

Twelve calendar-closed months ending August: {{ m("r12.new_customers") }} new
customers and {{ m("r12.m1_net") }} of first-month NET revenue. Comparing the
same eight months a year apart, January through August: customer count
{{ d("ytd26.new_customers", "ytd25.new_customers") }}
({{ m("ytd25.new_customers") }} to {{ m("ytd26.new_customers") }}), first-month
NET revenue {{ d("ytd26.m1_net", "ytd25.m1_net") }} ({{ m("ytd25.m1_net") }}
to {{ m("ytd26.m1_net") }}), average first order
{{ d("ytd26.avg_first_order", "ytd25.avg_first_order") }}
({{ m("ytd25.avg_first_order") }} to {{ m("ytd26.avg_first_order") }}).

Read that carefully. Revenue held because each new customer spent more, not
because we acquired as many. Fewer, larger orders is a mix effect, and deal
size is the more fragile of the two. August's strong average is welcome; it
is not yet a pattern.

The first-ninety-days figures cover only cohorts whose window has closed
(latest: the {{ m("m13.latest.cohort") }} cohort). Newer cohorts are shown as
first month only, because reporting a partial window as complete overstates
weakness.

## Where are customers coming from?

Over the twelve months, {{ m("r12.sources.customers") }} customers were
created and have since bought. The largest tracked first source is
{{ m("r12.sources.top_channel") }} at {{ m("r12.sources.top_share") }} of
them — but the largest bucket of all is no source at all:
{{ m("r12.sources.untracked_share") }} of new customers have nothing recorded
in the lead-source field. Until that falls, channel shares describe the
tracked minority, and any claim that one channel "drives" acquisition is
inference, not measurement. Closing the untracked gap is a NetSuite routing
and form-capture task, costs no media money, and is the single biggest
improvement available to this section.

## Are we spending wisely?

The approved marketing budget is {{ m("budget26.annual_approved") }}.
Cancelling World of Concrete in August released
{{ m("budget26.released_by_cancellation") }}, making the effective plan
{{ m("budget26.annual_effective") }}. Through August, true operating spend is
{{ m("ytd26.spend") }} against an effective plan-to-date of
{{ m("budget26.ytd_effective") }} — {{ c("budget26.vs_plan_story") }}. Two
corrections sit in the ledger and not in that figure: a
{{ m("corr26.seo_garageexperts_misbooking") }} SEO posting that belonged to
GarageExperts and was reversed, and an
{{ m("corr26.agency_credit_pending_detail") }} agency credit relating to last
year for which the agency has not yet supplied detail. We are trying to get
that detail. The credit stays on this year's books for the annual review and
moves no monthly figure.

Against last year, January through August: spend
{{ d("ytd26.spend", "ytd25.spend") }} ({{ m("ytd25.spend") }} to
{{ m("ytd26.spend") }}); first-month return per dollar
{{ m("ytd25.return_per_dollar") }} to {{ m("ytd26.return_per_dollar") }}; cost
per new customer {{ m("ytd25.cost_per_customer") }} to
{{ m("ytd26.cost_per_customer") }}.

A caution the ratio does not carry on its own: each month's spend is charged
against that month's customers. Last year's peak month,
{{ m("ytd25.peak_spend_month") }}, alone was {{ m("ytd25.peak_spend") }}, and
much of it produced customers in the months that followed, so last year's
efficiency reads worse than it was — and this year's cut, concentrated from
April, has so far been absorbed by deal size rather than proven costless. The
cost of a spend cut lands one to three months after it, which is exactly the
window this target is now measured in.

**The September ask, re-priced.** The approved budget bills agency fees at a
fifth of Google plus Meta spend, so the retargeting campaign costs
{{ m("ask26.retargeting.monthly_all_in") }} a month, not
{{ m("ask26.retargeting.monthly_media") }}: {{ m("ask26.retargeting.all_in") }}
for September through December. With the second customer focus group at
{{ m("ask26.focus_group.all_in") }}, both asks total {{ m("ask26.total") }}
and fit inside the released trade-show money with
{{ c("both_asks_fit_in_released_budget") }} to spare. This is a reallocation
within the approved plan, not new spend. Success measures: median time to
second order falls below {{ m("retention.median_days_to_second_order") }}
days, and the reorder rate on small first orders climbs off
{{ m("retention.under_400.rate") }}, measured in NetSuite on September-onward
cohorts against the pre-campaign baseline.

## Year-over-year headline

The structural finding is unchanged in direction and corrected in size. The
{{ m("vintage.pre2018_accounts") }} accounts acquired before 2018 are
{{ m("vintage.pre2018_share_of_accounts") }} of active accounts and
{{ m("vintage.pre2018_share_of_revenue") }} of last year's revenue, averaging
{{ m("vintage.pre2018_avg_annual_net") }} a year against
{{ m("vintage.band_2025_avg_annual_net") }} for an account acquired last year
— about {{ c("legacy_multiple") }}. Last month's deck said twenty; that was
the strongest single era, not the group. The corrected multiple still means
no realistic acquisition programme replaces a lost legacy account, and
protecting the accounts we have remains worth more than acquiring new ones.

These figures are the ones published in August, on the Sage created-date
basis. NetSuite cannot reproduce them because legacy accounts carry a
migration date rather than an acquisition date; they will refresh when a
Sage export is added to the repository. Until then they are labelled as
published, not as current.

## What changed in how we count

- Marketing spend is reported on a true-operating basis: GL as posted, with
  the {{ m("corr26.seo_garageexperts_misbooking") }} GarageExperts SEO posting
  removed from March and April, and August's
  {{ m("corr26.agency_credit_pending_detail") }} prior-year agency credit
  excluded from every monthly figure pending detail. August raw GL nets to
  {{ m("aug26.spend_as_posted") }} because both credits landed there; the
  true August figure is {{ m("aug26.spend_true") }}.
- The approved budget is {{ m("budget26.annual_approved") }}. Prior decks
  used a plan of roughly two hundred and ninety thousand that does not match
  the approved workbook.
- Previously published cohort months were re-pulled. {{ c("build.drift_story") }}
  The largest move was {{ m("build.drift_max_move") }}, downward; every move
  is logged in the restatement report and none is applied.
- Agency-reported "platform revenue" and return are excluded from every
  figure here. For January–August the agency dashboard reports
  {{ m("truad.platform_revenue_ytd") }} of revenue on
  {{ m("truad.media_ytd") }} of media; the customers acquired this year have
  actually produced {{ m("ytd26.revenue_to_date") }}. That is a
  {{ m("truad.revenue_overstatement") }} overstatement, and it is the kind
  of number that miscalibrates expectations if it circulates.
- The first-ninety-days window now starts on the day a record was created,
  not at the exact timestamp. The previous basis dropped same-day orders
  placed after the record's creation time.
