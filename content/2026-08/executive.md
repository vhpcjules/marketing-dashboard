---
period: 2026-08
dashboard: executive
prepared: 2026-09-05
claims:
  legacy_multiple:
    expr: "fy25.vintage_pre2018_avg_annual_net / fy25.vintage_2025_avg_annual_net"
    assert: "between(9, 13)"
    render: "{:.0f}×"
    note: "v1 published 20×. That was the 2010–2012 band alone ($47,507) against 2025; across all 275 pre-2018 accounts the average is $25,574 and the multiple is about 11×."
  required_uplift_sep_dec:
    expr: "delta(target26.still_needed, fy25.m1_sep_dec)"
    assert: "between(40, 60)"
    render: "{:+.0f}%"
  both_asks_fit_in_released_budget:
    expr: "budget26.woc_released - (ask26.retargeting_all_in + ask26.focus_group)"
    assert: "positive"
    render: "${:,.0f}"
  roas_2026_exceeds_fy2025_at_a_third_of_maturity:
    expr: "ytd26.roas_to_date - fy25.roas_to_date"
    assert: "positive"
    render: "${:.2f}"
not_carried_forward:
  - "v1 Budget: '$33,177 under YTD' and '$56K–$104K of headroom'. The approved budget is $206,346, not $291,545; Jan–Jul was over plan, not under. Corrected below."
  - "v1 Budget: 'Google 58% under-spent, $65,630 available'. Jan–Mar Google spend sat in the Advertising account before the split; combined, Google ran 4% under plan. Corrected below."
  - "v1 YoY: 'a legacy account is worth 20× a new one'. The all-pre-2018 average is about 11×. Corrected below; still the load-bearing finding."
  - "v1 Leadership: the 8-wedge source donut. Replaced by a sorted bar in one colour."
  - "v1 Leadership: 'Are we spending wisely?' channel ROI table with an OVERALL row that did not sum ($260,250 gap). Rebuilt from the data layer with a computed total."
---

# Marketing performance — August 2026

## The three things to take from this month

### 1. Hitting the 19% target now requires a 50% second-half acceleration

Leadership's target is {{ m("target26.growth_pct") }} growth in new-customer
first-month NET revenue over 2025. Through August we have booked
{{ m("ytd26.m1_net_revenue") }} against a full-year target of
{{ m("target26.amount") }}. The four remaining months must deliver
{{ m("target26.still_needed") }}, or {{ m("target26.required_monthly") }}
every month — {{ c("required_uplift_sep_dec") }} above the same four months
last year, after a first eight months that ran roughly flat. At the May–August
run rate of {{ m("ytd26.run_rate_may_aug") }} a month, the year lands at
{{ m("target26.forecast_at_run_rate") }}, or
{{ d("target26.forecast_at_run_rate", "fy25.m1_net_revenue") }} against
2025. The gap is {{ m("target26.gap") }}.

That gap is nearly closable with money already released. At the year-to-date
first-month return of {{ m("ytd26.roas_m1") }} per dollar, closing it needs
about {{ m("target26.spend_to_close_at_m1_roas") }} of additional spend;
cancelling World of Concrete released {{ m("budget26.woc_released") }} and the
year is running {{ m("budget26.under_effective_plan") }} under the effective
plan, so the shortfall is roughly {{ m("target26.shortfall_after_available") }}.
On a more cautious marginal return of two dollars and fifty cents, the ask
rises to about {{ m("target26.shortfall_after_available_conservative") }}.
Either way it is a September decision, not an October one: the window is
four months and the creation-to-first-order lag eats into it.

### 2. August rebounded; July was a dip, not a trend

August brought {{ m("aug26.new_customers") }} new customers, the most of any
month this year, and {{ m("aug26.m1_net_revenue") }} of first-month NET
revenue at an average first order of {{ m("aug26.avg_first_order") }}. Last
month's {{ m("jul26.avg_first_order") }} average — flagged then as an early
warning on deal size — did not repeat. One month is one month; August says
July was the outlier.

August marketing spend was {{ m("aug26.spend_true") }}. Two cautions on that
figure: August is still an open period, and no agency fee has posted for it
yet (the approved plan carries {{ m("budget26.agency_monthly") }} a month), so
expect the final August figure to be somewhat higher.

### 3. First-month return understates what the spend bought by about half

Judged on first-month revenue alone, every marketing dollar this year has
returned {{ m("ytd26.roas_m1") }}. But the customers acquired this year have
kept buying: their revenue to date is {{ m("ytd26.cohort_revenue_to_date") }},
of which {{ m("ytd26.repeat_share_pct") }} arrived after their first month.
On that basis the return is {{ m("ytd26.roas_to_date") }} per dollar — already
{{ c("roas_2026_exceeds_fy2025_at_a_third_of_maturity") }} ahead of the full
2025 class's {{ m("fy25.roas_to_date") }}, with the 2026 cohorts averaging
{{ m("ytd26.avg_maturity_months") }} months of age against
{{ m("fy25.avg_maturity_months") }}. First-month return closes fast enough to
steer on; revenue-to-date is the truer measure of what the money bought. Both
are shown, always labelled, and the target is graded on the first.

## Are we growing?

Twelve calendar-closed months ending August: {{ m("r12.new_customers") }} new
customers and {{ m("r12.m1_net_revenue") }} of first-month NET revenue.
Comparing the same seven months a year apart, January through July: customer
count {{ d("jj26.new_customers", "jj25.new_customers") }}
({{ m("jj25.new_customers") }} to {{ m("jj26.new_customers") }}), first-month
NET revenue {{ d("jj26.m1_net_revenue", "jj25.m1_net_revenue") }}
({{ m("jj25.m1_net_revenue") }} to {{ m("jj26.m1_net_revenue") }}), average
first order {{ d("jj26.avg_first_order", "jj25.avg_first_order") }}
({{ m("jj25.avg_first_order") }} to {{ m("jj26.avg_first_order") }}).

Read that carefully. Revenue held because each new customer spent more, not
because we acquired as many. Fewer, larger orders is a mix effect, and deal
size is the more fragile of the two. August's strong average is welcome; it
is not yet a pattern.

The first-90-days figures below cover only cohorts whose window has closed
(through {{ m("cohorts.last_closed_m13_month") }}). Newer cohorts are shown
as first month only, because reporting a partial window as complete
overstates weakness.

## Where are customers coming from?

{{ m("r12.source_mix_note") }}

## Are we spending wisely?

The approved 2026 marketing budget is {{ m("budget26.annual_approved") }}.
Cancelling World of Concrete in August released {{ m("budget26.woc_released") }},
making the effective plan {{ m("budget26.annual_effective") }}. Through
August, true operating spend is {{ m("ytd26.spend_true") }} against an
effective plan-to-date of {{ m("budget26.ytd_effective") }} —
{{ m("budget26.under_effective_plan") }} under. Two corrections sit in the
ledger and not in that figure: a {{ m("corr26.seo_misbooking") }} SEO posting
that belonged to GarageExperts and was reversed, and an
{{ m("corr26.agency_credit") }} agency credit relating to 2025 for which the
agency has not yet supplied detail. The second stays on this year's books for
the annual review and moves no monthly figure.

Against last year, January through July: spend
{{ d("jj26.spend_true", "jj25.spend") }} ({{ m("jj25.spend") }} to
{{ m("jj26.spend_true") }}); first-month return per dollar
{{ m("jj25.roas_m1") }} to {{ m("jj26.roas_m1") }}; cost per new customer
{{ m("jj25.cost_per_customer") }} to {{ m("jj26.cost_per_customer") }}.

A caution the ratio does not carry on its own: month-M spend is charged
against month-M customers. April 2025 alone was {{ m("apr25.spend") }} and
much of it produced May–July customers, so 2025's efficiency reads worse than
it was, and 2026's cut — concentrated from April — has so far been absorbed
by deal size rather than proven costless. The cost of a spend cut lands one
to three months after it, which is exactly the window this target is now
measured in.

**The September ask, re-priced.** The approved budget bills agency fees at
twenty percent of Google plus Meta spend, so the retargeting campaign costs
{{ m("ask26.retargeting_monthly_all_in") }} a month, not
{{ m("ask26.retargeting_monthly_media") }}: {{ m("ask26.retargeting_all_in") }}
for September through December. With the second customer focus group at
{{ m("ask26.focus_group") }}, both asks total {{ m("ask26.total") }} and fit
inside the released trade-show money with
{{ c("both_asks_fit_in_released_budget") }} to spare. This is a reallocation
within the approved plan, not new spend. Success measures: median time to
second order falls below {{ m("retention.median_days_to_second_order") }}
days, and the reorder rate on first orders under four hundred dollars climbs
off {{ m("retention.reorder_rate_under_400") }}, measured in NetSuite on
September-onward cohorts against the pre-campaign baseline.

## Year-over-year headline

The structural finding is unchanged in direction and corrected in size. The
{{ m("fy25.vintage_pre2018_accounts") }} accounts acquired before 2018 are
{{ m("fy25.vintage_pre2018_share_of_accounts") }} of active accounts and
{{ m("fy25.vintage_pre2018_share_of_revenue") }} of 2025 revenue, averaging
{{ m("fy25.vintage_pre2018_avg_annual_net") }} a year against
{{ m("fy25.vintage_2025_avg_annual_net") }} for an account acquired in 2025 —
about {{ c("legacy_multiple") }}. Last month's deck said twenty; that was the
strongest single era, not the group. Eleven still means no realistic
acquisition programme replaces a lost legacy account, and protecting the
accounts we have remains worth more than acquiring new ones.

{{ m("lapsed.summary_note") }}

## Are we connecting with people online?

{{ m("online.summary_note") }}

## What needs attention

{{ m("flags.executive") }}

## What changed in how we count

- Marketing spend is reported on a true-operating basis: GL as posted, with
  the {{ m("corr26.seo_misbooking") }} GarageExperts SEO posting removed from
  March and April, and August's {{ m("corr26.agency_credit") }} prior-year
  agency credit excluded from every 2026 monthly figure pending detail.
  August raw GL nets to {{ m("aug26.spend_as_posted") }} because both credits
  landed there; the true August figure is {{ m("aug26.spend_true") }}.
- The approved budget is {{ m("budget26.annual_approved") }}. Prior decks
  used a plan of roughly two hundred and ninety thousand that does not match
  the approved workbook.
- Nineteen previously published cohort months were re-pulled. Five moved,
  all downward, by up to {{ m("drift.max_move_pct") }}; the published figures
  are held frozen and the moves are logged for review.
- Agency-reported "platform revenue" and ROAS are excluded from every figure
  here. For January–August the agency dashboard reports
  {{ m("truad.platform_revenue_ytd") }} of revenue on
  {{ m("truad.media_ytd") }} of media; the customers it acquired have
  actually produced {{ m("ytd26.cohort_revenue_to_date") }}. That is a
  {{ m("truad.revenue_overstatement_x") }} overstatement, and it is the kind
  of number that miscalibrates expectations if it circulates.
