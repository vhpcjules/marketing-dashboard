# Methodology

The rules every number on every dashboard obeys. Encoded once, in code, with
the reasoning in comments. Never re-derived inline.

If you change anything here, the tests in `tests/` will tell you what you
broke. If they don't, the change wasn't guarded and that's a bug in the tests.

---

## NET revenue

Every revenue figure is **NET**: product only, excluding shipping and tax,
with returns and credit memos deducted. There is no gross figure anywhere in
the output, and the word "gross" is a build failure.

Canonical query: `src/data/queries/net_revenue_monthly.sql`. Every clause is
load-bearing and the file says why. The three that bite:

- **`i.itemtype IS NOT NULL`** — freight and shipping items (FedEx, UPS, USPS,
  SEFL, warehouse) post as items with a NULL itemtype. Omitting this added
  $11,703 of shipping to Jan–Jul 2026 "NET" revenue in v1.
- **All four transaction types** — `CustInvc`, `CashSale`, `CustCred`,
  `CustRfnd`. Drop the last two and returns vanish.
- **`COALESCE`** — without it `SUM()` returns NULL, not 0, for an empty period.

Two additions to the inherited methodology:

**The itemtype exclusion is measured, not assumed.** Ingest runs the revenue
query with and without that clause and asserts the difference equals the
freight total. An inner join plus a NOT NULL filter does two jobs and one is
invisible: a line whose item doesn't resolve disappears silently. Without the
reconciliation you cannot distinguish *"we correctly excluded $11,703 of
freight"* from *"we lost $11,703 of product revenue to a join failure."*

**The subsidiary filter is in the SQL.** The inherited spec named
`VHPC LLC` in prose but omitted the predicate from the canonical join.
`c.category NOT IN (...)` excludes GarageExperts as *customers*, which is a
different axis from subsidiary. Confirmed: `ns_getSubsidiaries` returns
exactly one real subsidiary, id **2**, *Versatile High-Performance Coatings,
LLC*.

**Category IDs are resolved by name and asserted at ingest.** The two source
documents disagree about which ID is which — the build prompt and fixture say
`2 = GarageExperts, 14 = Vendor`; the v1 YoY footer says the reverse. Both are
excluded so revenue is unaffected, but the disagreement means the
documentation is unreliable, so the mapping is verified rather than trusted.

## Customer vs Lead

A record is a **Customer** when `stage` starts with `"Customer-"` **and**
`firstorderdate` is populated. Stage alone is insufficient.

## Cohorts and revenue windows

| Term | Definition |
|---|---|
| Cohort | `customer.datecreated` month — not first-order month |
| M1 | Revenue in the cohort's own creation month |
| M1–3 | `trandate >= datecreated AND trandate < datecreated + 90` |
| Lifetime-to-date | All NET revenue for the cohort through the reporting date, always with elapsed maturity stated |

**M1–3 windows are not reported until closed.** `periods.py` computes which
are closed from the as-of date rather than relying on a hardcoded month list.

**Two known issues with the M1 basis, carried as diagnostics rather than
changed:**

1. Because a Customer requires `firstorderdate` but the cohort is keyed on
   `datecreated`, any customer whose sales cycle crossed a month boundary
   contributes **zero** M1 revenue by construction. So M1 return per dollar is
   a **floor**, not an estimate, and the narrative must say "at least".
2. It makes the headline partly a function of record-creation discipline —
   create the record at quote time and revenue leaves M1; create it at order
   time and it stays. The `firstorderdate − datecreated` lag distribution is
   computed every month as a standing diagnostic, because a shift in that lag
   masquerades as a shift in demand.

**"M1–3" is labelled "first 90 days" in output.** The SQL is 90 days; three
calendar months is a different window at cohort edges (for a customer created
31 January, three months is 89 days). The label matches the arithmetic.

## Marketing spend

GL `66212.*` and `66215.*`. **Exclude `96212.*`** — the NAF, the GarageExperts
franchisee fund. Confirmed present with 21 sub-accounts that mirror the
marketing chart of accounts almost line for line, so include patterns must
stay anchored: `LIKE '%6212%'` matches both.

**Budget is not in NetSuite.** Report `-197` (Budget vs. Actual) returns
`Budget Amount` of 0 for every marketing account *and at the subsidiary
grand-total line*. The approved budget lives in
`data/manual/2026/approved_marketing_budget.json`, transcribed from
`2026 Approved Marketing Budget.xlsx` (confirmed authoritative 2026-09-04).

**Agency fees are derived, not fixed:** `0.20 × (Google + Meta)`. Any paid
media ask carries a 20% agency surcharge and must be priced with it.
`price_ask()` does this.

**Cancelled budget is released, not underspent.** World of Concrete was
cancelled in August 2026, releasing $24,500 (Aug/Nov/Dec of `66212.0007`). A
line the business chose not to execute is a different fact from a line it
failed to execute, and budget-vs-actual says which.

### The three spend bases

Spend is not one number. Corrections posted in one month can belong to a
different month — or a different **year**.

| Basis | Contents | Used for |
|---|---|---|
| `AS_POSTED` | Raw GL | What the ledger says for a window; what the freeze holds |
| `TRUE_OPERATING` | VHPC's real activity, by the month it happened | **All efficiency metrics** |
| `ANNUAL_LEDGER` | As-posted including prior-year corrections | Annual budget-performance review only |

August 2026 is why. Raw GL nets to **−$9,493** because two credits landed that
month, which published unguarded would have made every August efficiency
metric negative. The two credits are not the same kind of thing:

- **SEO `66212.0013`, −$9,453.75** — 2026 spend (March $6,500, April $2,953.75)
  that belonged to GarageExperts. Never VHPC's, so it **restates March and
  April down**. True Jan–Jul spend is **$127,437.03**, not $136,890.78.
- **Agency Fees `66212.0002`, −$8,528.87** — a **2025** mischarge credited on
  the 2026 ledger. It stays on 2026's books for the annual review but is
  **excluded from every 2026 monthly figure and every efficiency metric**,
  because it is not 2026 marketing activity.

August true operating spend: **$8,489.18**.

## Percent versus points

**Never display a percentage-point difference.** Every delta is a relative
percent change:

    change = (current − previous) / previous × 100

There is exactly **one** delta function: `src.units.delta`. If a metric is
itself a percentage its delta is still relative — 45.6% → 55.7% is **+22.1%**,
not "+10.1 points".

This is enforced structurally, not by convention. `Pct − Pct` returns
`PctPoints`, whose `__format__` raises `PointDifferenceError` with a message
naming the correct figure. A point difference cannot reach a template.

Ranges are fine and need no delta: "engagement rate climbed 36.9% → 52.0%".
Ranges are the **default** rendering for percentage metrics; relative deltas
are the default for counts and currency. That way the trap value is never
computed where it is tempting.

The forbidden-string check (`pt`, `pts`, `pp`, `percentage point`, `points`)
runs on **word boundaries, against rendered text nodes only**. A naive
substring scan fails on "phone ca**pt**ure" and "o**pp**ortunity" — the very
metrics it exists to protect — and on `font-size: 12pt` in CSS.

## Rates and sample size

Any published rate carries its denominator. Differences inside the noise band
are described as "no measurable difference", never ranked.

This exists because the Sales page names three people. Alexis 59/29 = 49.2%
against Dan 42/15 = 35.7% looks like a 13.5-point gap; on those denominators
it is about 1.4 standard errors — indistinguishable from noise. Publishing it
as a ranking would create a false performance story about named colleagues.

## Source-defect guards

Every ingest records its coverage window and row count. **A metric whose
source window does not fully cover the reporting period is refused, not
flagged** — a refusal cannot be overlooked, a flag can. This is the mechanical
version of "never treat a partial month as final", which as a process
instruction cost five months of 2× understated LinkedIn impressions.

- **LinkedIn** — `page_impressions` / `page_engagements` /
  `page_engagement_rate` come from PageStatistics. `total_share_impressions`
  comes from share_statistics and cannot be broken down by date. Never mixed.
- **Meta lead forms** — `onsite_conversion.lead_grouped`. The pixel field
  `offsite_conversions_fb_pixel_lead` is entirely NULL for this account.
- **Meta objectives** — campaigns run `OUTCOME_LEADS`, `OUTCOME_SALES` and
  `OUTCOME_TRAFFIC`. Never judge an awareness or traffic campaign on a lead
  metric. `campaignobjective` is pulled alongside performance, always.
- **GMB and Hotjar** — arrive as manual files via `data/manual/` without
  blocking the build.

## Two ROAS figures, never one

M1 is the target frame, but it is not what the spend bought. A customer
acquired in March who reorders in June produces revenue M1 never sees, and
the M1 window closes long before most reordering happens — median time to a
second order is 17 days, but only 51% of eventual repeat buyers have
reordered by then and 83% by day 90.

So both are reported, side by side, always:

| Basis | Jan–Aug 2026 | ROAS |
|---|---|---|
| M1 NET revenue | $552,656 | **$4.07** |
| + repeat revenue since | $478,566 | |
| = revenue to date | $1,031,222 | **$7.59** |

Repeat revenue is **46.4%** of everything those cohorts have produced.
Judging marketing on M1 alone credits it with roughly half of what its
customers have already spent.

**`Roas` cannot be constructed without a basis label and a maturity**, in the
same way `Money` cannot be constructed without a period. An M1 figure and a
to-date figure differ by about 2× at four months of maturity and 5× at
fourteen; a ROAS quoted without both labels is a number waiting to be
misread.

**Maturity is the whole caveat.** 2026 cohorts average 4.3 months against
14.3 for 2025, and the multiple climbs steeply with age:

| Cohort age | Revenue-to-date multiple of M1 |
|---|---|
| 1 month (2026-08) | 1.13× |
| 3 months (2026-06) | 1.28× |
| 6 months (2026-03) | 2.61× |
| 8 months (2026-01) | 3.34× |
| ~14 months (2025 cohorts) | 5.09× |

Every aggregate reports the **customer-weighted** average maturity that
produced it — month-weighting would let a 55-customer month pull the average
as hard as a 127-customer one.

Revenue-to-date is a **reading frame, not a target**. Neither ROAS basis is
the company target (next section); both describe what marketing's spend
bought.

## The target, restated 2026-09-05

Leadership's 19% is growth in **total company NET revenue** over 2025, not in
new-customer revenue (confirmed by Jules 2026-09-05; the 2026-09-04 reading
that it applied to M1 is recorded as superseded in the budget file's
`targets`). Consequences for the build:

- The target series is `revenue_total`: monthly total NET revenue, all
  customers, same canonical join as the cohort queries without the cohort
  restriction (`src/ingest/queries/revenue_total_monthly.sql`). FY2025 on
  that basis is $20,590,950.41 (every transacting account, from the
  acquisition-vintage pull); the target is $24,503,230.99.
- Until the monthly series is ingested the pages show the target and mark
  **pace as pending**. Nothing is estimated in its place.
- M1 and revenue-to-date remain **marketing's contribution frame**: the
  revenue of the customers marketing acquired, a subset of the total formed
  by the same join. They are shown alongside the target with their growth
  rate, never graded as if they were the target.
- "Spend to close the gap" is computed only once the total series exists,
  at this year's revenue-to-date return per marketing dollar (an average,
  so it understates the marginal cost) and at a cautious $2.50.

## The freeze decision, 2026-09-04

Closed periods keep their frozen snapshot values. Months never previously
published use live figures. The −1.1% (2025) and −1.2% (2026) cohort drift
found on 2026-09-04 is logged in `reports/restatement_2026-08.md` and
**deliberately not applied**.

This matters for the marketing frame: FY2025 M1 is held at the published
**$878,098**, not the $872,631 a live pull returns today. Without that pin,
every year-over-year growth figure would silently re-baseline on each build.

## Findings from the 2026-09-05 data pulls

Eight metric families were pulled from NetSuite and reconciled against the
August 18 build. Four changed the methodology; two could not be reproduced.

**First 90 days uses the customer's creation *date*, not timestamp.**
`customer.datecreated` carries a time of day. The inherited SQL
`trandate >= datecreated` therefore excluded a first order placed earlier on
the creation day than the record itself — which is common, since the record
is often created at order entry. The published matched Jan–Apr figures
($460,932 / $310,602; $1,945 / $1,618 per customer) reproduce *to the cent*
on that flawed basis. On the corrected basis
(`trandate >= TRUNC(datecreated) AND trandate < TRUNC(datecreated) + 90`)
first-90-days revenue is about 20% higher in both years, and the year-over-
year story survives (−30.6% on matched Jan–Apr against −32.6% before). The
corrected basis is the one used. `src/data/queries/cohorts_m13.sql` documents
both. A related v1 defect: the Leadership chart's Sep 2025 first-90-days bar
of $308,048 was the cohort's *lifetime* revenue, not its 90-day figure.

**The acquisition-vintage table cannot be produced from NetSuite.** Every
account migrated at go-live carries a `datecreated` equal to the migration
load date (Aug–Oct 2023 or Sep 2024), so no pre-2023 era exists in the
system. v1 used Sage created dates from a workbook. All 108 entity custom
fields were checked; none carries the original date. Sage account numbers
survive in `entityid` and are sequential, so a Sage export or a
number→year anchor table supplied as a manual file would restore the eras.
Until then the vintage section renders as pending, and the published pre-2018
figures (275 accounts, 34.3% of FY2025 revenue, about 11× a 2025 account) are
carried with their source named and their non-reproducibility stated. FY2025
revenue totals *do* reconcile within 0.5%; the published active-account count
(2,676) is ~31% above anything NetSuite returns (2,038–2,093) and most
likely counted sales orders rather than NET-positive invoices.

**"First order" is the first NET-positive transaction, not `firstorderdate`.**
46.5% of customers have zero NET on their `firstorderdate` (the field is set
at order entry, before invoicing). Retention bands use the first NET-positive
date. On that basis the under-$400 band reorders at 33.1% (published 35.4%),
the median time to second order is 22 days (published 17), and 80.9% of
eventual reorders happen inside 90 days (published 83%). The 90-day
retargeting window is still correctly sized; the published under-$400
population (~915 implied) could not be reproduced from any variant (703 live).

**Phone capture uses a NULL-safe test.** In SuiteQL the empty string *is*
NULL, so `TRIM(phone) <> ''` is never true and returns zero for every month.
`COUNT(TRIM(phone))` is the equivalent test. Published July 2026 phone
capture of 55.7% reads 56.9% today because 22 July records gained a phone
number after the build — conversion and capture are to-date measures and rise
until frozen.

**Customer mix and source mix do not reproduce v1's buckets.** The published
12-month mix (1,055 customers, $900,702 M1) matches no 12-month `datecreated`
window; its M1 total exceeds the frozen cohort sum by $24,578, consistent
with the shipping-inclusion bug. Live: Contractor 81% of customers and 87.5%
of M1 (published 85% / 91%); 12–13 customers were reclassified Contractor →
DIY after August 18. Lead-source buckets: only Organic Search and Trade Show
reproduce; Paid Google is 165 (published 193), Direct 192–219 (328), Email
10 (27). The raw `leadsource` → bucket mapping is recorded in the source_mix
query header. **Untracked share is rising steeply in the two newest cohorts**
(56% of July customers, 70% of August) — partly because recent leads have not
converted yet, partly a real attribution gap worth a flag.

**Lead routing reproduces within one or two records per rep-month.** The
differences are to-date conversions and records deleted or re-dated since
August 18 (e.g. July 2026: Alexis 59/30 vs published 59/29; Parker 71/33 vs
72/31). v1's own page carried three different July totals (277, 278, 287);
the live definition gives 276.

**Geography** reproduces in total (1,011 vs 1,028) and in the TX+CA share
(43%), with individual states moving ±5 in both directions since August 18.
State comes from the default shipping address-book entry; `shipstate` is not
exposed to SuiteQL.

**Reading frozen values against live ones.** Published figures were rounded
to the dollar. Drift detection ignores differences of 50 cents or less so a
cent-level live value is not reported as movement.

## Account vintage from Sage sales history, 2026-09-05

Jules supplied the Sage "Customer Sales History by Period" reports for 2019
through September 2024 (`data/manual/sage/customer_sales_history_2019_2024.json`,
7,686 Sage customers, annual net dollars sold). They are not a created-date
export: the earliest year is 2019, so an account already selling then is
**"2019 or earlier"** — a floor, never a birth year — and the pre-2018 band
v1 published cannot be recovered from them.

The join is offline (`src/data/vintage.py`): the Sage customer number is
expected to survive migration as the first token of the NetSuite `entityid`.
A `vintage_accounts` pull (`src/ingest/queries/vintage_accounts.sql`) returns
per-account FY revenue with `entityid`; Sage dates every account it saw, the
NetSuite creation year dates the rest. The Executive page switches from the
published figures to the computed table the month that pull lands, and says
which basis it is on.
