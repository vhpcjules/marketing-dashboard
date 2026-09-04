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
