# Restatement log — August 2026

Generated 2026-09-04. Two corrections posted in August 2026 affect previously
reported figures. Per the freeze rule, no closed-period snapshot was
overwritten; the corrections are recorded here and applied by basis.

## 1. SEO misbooking — GarageExperts, $9,453.75

| | |
|---|---|
| Account | `66212.0013` Advertising & Marketing : Search Engine Optimization |
| Originally posted | March 2026 $6,500.00 · April 2026 $2,953.75 |
| Credited | August 2026, −$9,453.75 (full reversal) |
| Classification | Current-year spend that was never VHPC's |

A missed Scorpion posting was booked to VHPC but belongs to GarageExperts.

**Effect.** March restates $35,919.78 → $29,419.78. April restates
$9,223.14 → $6,269.39. True VHPC Jan–Jul spend is **$127,437.03**, against
**$136,890.78** as previously published.

Efficiency on the corrected basis, Jan–Jul 2026:

| | As published | Corrected |
|---|---|---|
| Return per $1 (M1) | $3.46 | **$3.71** |
| Cost per new customer | $306 | **$285** |
| Spend as share of M1 revenue | 28.9% | **26.9%** |
| Spend change vs 2025 | −58.0% | **−60.9%** |

## 2. Agency fees — 2025 mischarge, $8,528.87

| | |
|---|---|
| Account | `66212.0002` Advertising & Marketing : Agency Fees |
| Credited | August 2026, −$8,528.87 |
| Classification | Prior-year (2025) correction, landing on 2026's ledger |

**Effect on 2026 monthly measurement: none, by decision.** It remains on
2026's books and appears in the annual budget-performance review, but it is
not 2026 marketing activity and does not move any monthly spend figure or
efficiency metric.

**Open question.** Whether 2025's reported Jan–Jul spend of $326,229 should
also be reduced by $8,528.87 for a like-for-like year-over-year comparison
depends on what the mischarge actually was:

- If it was **charged to VHPC but belonged elsewhere**, 2025 true spend was
  $317,700 — and 2025 return per $1 becomes $1.52, cost per customer $575.
- If it was an **agency overbilling later refunded**, same conclusion.
- If it was **misallocated between accounts within VHPC**, 2025's total is
  unchanged and only the channel split moves.

The year-over-year spend headline is the most-read number on the Executive
page, so this matters. Pending an answer, 2025 is reported as originally
published and this note stands.

## Consequence for August reporting

Raw GL for August 2026 nets to **−$9,493.44**. Published unguarded, every
August efficiency metric inverts — return per dollar and cost per customer
both go negative.

August is reported as **$8,489.18** of operating spend (Google $5,866.79,
Meta $2,596.42, Other $25.97), with the −$17,982.62 of corrections shown as
their own line rather than blended into the month.

Guarded by `tests/test_spend.py::TestAugustIsNotNegative` and by the
`credits_blended_into_monthly_spend` mutation, which reintroduces the fault
and confirms the suite goes red.

---

# Addendum — 2025 agency overcharge, and cohort drift

## 3. The agency credit is a 2025 overcharge (resolved 2026-09-04)

Jules confirms the $8,528.87 agency credit was **an overcharge from last
year**. So 2025's reported spend was overstated, and a like-for-like
year-over-year comparison must correct both sides:

| Jan–Jul | As published | Corrected |
|---|---|---|
| 2025 spend | $326,229 | **$317,700** |
| 2025 return per $1 | $1.48 | **$1.52** |
| 2025 cost per customer | $591 | **$576** |
| 2026 spend | $136,891 | **$127,437** |
| 2026 return per $1 | $3.46 | **$3.71** |
| Spend change YoY | −58.0% | **−59.9%** |

**Stated assumption:** the full $8,528.87 is attributed to the Jan–Jul 2025
window. If the overcharge accrued across all twelve months of 2025, only
about $4,975 belongs to Jan–Jul, and 2025's corrected return per $1 would be
$1.50 rather than $1.52. Attributing the whole amount is the conservative
choice — it improves 2025 and therefore *understates* 2026's improvement.
Worth pinning down which months the overcharge covered.

## 4. Cohort drift — 5 of 14 months have moved (unexplained)

A live pull on 2026-09-04 reproduces **9 of the 14 published cohort months to
the cent**, confirming the NET revenue methodology. Five have moved, all
downward:

| Cohort | Customers | M1 NET | Drift |
|---|---|---|---|
| 2025-03 | 91 → 89 | $90,201 → $88,906 | −1.44% |
| 2025-05 | 94 → 93 | $77,932 → $74,096 | −4.92% |
| 2025-06 | 88 → 87 | $71,999 → $71,662 | −0.47% |
| 2026-06 | 67 → 63 | $130,063 → $125,591 | −3.44% |
| 2026-07 | 72 → 70 | $51,088 → $49,741 | −2.64% |

Aggregate: 2025 Jan–Jul **−1.14%**, 2026 Jan–Jul **−1.23%**. Both exceed the
1% drift threshold, so this would fail a build under the freeze gate. **No
snapshot has been overwritten.**

**Ruled out:** GarageExperts reclassification. The category-2 exclusions were
tested directly and fall in different months entirely (2025-01, 2025-06,
2025-08, 2025-09, 2026-01, 2026-03, 2026-05 — a single recurring entity at
roughly $20K/month, correctly excluded).

**Leading hypothesis:** credit memos and returns posted after the original
report date. Supporting evidence: the unaffected months match to the *cent*,
so this is not a methodology difference but transaction-level change in
specific months; and every move is downward, which is what late credits look
like.

**Decision needed:** accept the restatement (regenerate the snapshots in
their own commit) or investigate the five months first. Under the freeze rule
this is Jules's call, not the tool's.

---

# Addendum 2 — the overcharge is 2026, and the reconciliation does not explain it

Corrected by Jules 2026-09-05: the agency overcharge is a **2026** item, not a
2025 mischarge. Two consequences.

**FY2025 carries no adjustment.** Reverting the correction I applied in
Addendum 1: Jan–Jul 2025 spend is $326,229 and return per $1 is $1.48; FY2025
spend is $591,782.91, M1 ROAS $1.47, revenue-to-date ROAS $7.51.

**2026 absorbs both corrections.** Jan–Jul true spend is **$118,908.16**
(as posted $136,890.78, less the $9,453.75 GarageExperts SEO misbooking and
the $8,528.87 agency overcharge). Jan–Aug true spend is **$127,397.34**.

| Jan–Jul 2026 | As posted | True |
|---|---|---|
| Spend | $136,891 | **$118,908** |
| Return per $1 (M1) | $3.46 | **$3.98** |
| Cost per new customer | $306 | **$266** |
| Spend as share of M1 revenue | 28.9% | **25.1%** |
| Spend change vs 2025 | −58.0% | **−63.6%** |

## The reconciliation: what the TRUAD data actually shows

Eight months of TRUAD "Overall Performance" media spend, reconciled against
the GL.

**Media reconciles.** Jan–Jul TRUAD media $68,228.97 against GL $67,617.83 —
a 0.90% difference. The channel mapping holds (Paid Search + Performance Max
= Google, Paid Social = Meta).

**The agency fee does not show an overcharge.** At the contracted
`0.20 × actual media spend`:

| | Billed | 0.20 × TRUAD media | Over / (under) |
|---|---|---|---|
| Jan | $2,393.00 | $2,323.48 | +$69.52 |
| Feb | $2,393.00 | $2,346.35 | +$46.65 |
| Mar | $2,393.00 | $2,349.00 | +$44.00 |
| Apr | $0.00 | $1,663.60 | −$1,663.60 |
| May | $1,700.00 | $1,633.39 | +$66.61 |
| Jun | $1,700.00 | $1,668.22 | +$31.78 |
| Jul | $1,700.00 | $1,661.74 | +$38.26 |
| **Total** | **$12,279.00** | **$13,645.79** | **−$1,366.79** |

The agency was **under-billed by $1,366.79**, not over by $8,528.87. So the
$8,528.87 credit is **not explained by the fee formula against actual media
spend**, and the month attribution remains unresolved.

Hypotheses tested and rejected:

- **Media overspend** — Jan–Jul media budget $69,898 against $67,618 actual.
  Under, not over.
- **Fee on search + social only** (excluding Performance Max) — $7,668.35 due,
  over by $4,610.65. Wrong magnitude.
- **Fee on Meta only** — $4,092.62 due, over by $8,186.38. Closest, but still
  $342.49 out, and there is no contractual basis for it.
- **The residual** — $12,279.00 − $8,528.87 = $3,750.13. No formula on any
  combination of the eight months produces this figure.

**April is the one month worth a second look.** GL media is $2,148.63 *lower*
than TRUAD, and no agency fee was posted at all. Every other month agrees
within ~$860. April looks like a month with missing or late postings.

## How it is handled until explained

The credit reduces the **year-to-date total** and is assigned to **no month**.
Guessing a month would corrupt a monthly series that is otherwise defensible
to the cent.

A consequence, and it is deliberate: a TRUE_OPERATING monthly series sums to
**$8,528.87 more** than its window total. `monthly_sum_gap()` names the
difference so no consumer discovers it by accident, and every month is
asserted non-negative.

**What would close this:** the agency's invoice detail for the credit, or an
explanation from accounting of what the $8,528.87 covered.

## A separate and more serious finding: TRUAD's revenue column

TRUAD reports a "Platform Revenue" column and a ROAS chart. For Jan–Aug 2026
it reports **$7,092,965.84 of revenue on $76,357.42 of media — an implied
ROAS of $92.89 per $1**, with the chart displaying month values from $36 to
$216 per dollar.

The 2026 cohorts have actually produced **$1,031,222.11** of NET revenue to
date against $127,397.34 of total marketing spend — a real ROAS of **$8.09**.

TRUAD overstates revenue by **6.9×** and ROAS by roughly **11×**. This is
platform-reported conversion value with the attribution window and
double-counting that implies; it is not comparable to NetSuite NET revenue on
any basis.

**It must never appear on a VHPC dashboard, feed any ROAS figure, or be
quoted to leadership.** Recorded in
`data/manual/2026/truad_media_spend.json` as `platform_revenue_warning`.

## One inference upgraded to confirmed

The `66212.0020` Advertising → Google reclassification is no longer an
inference. GL account `66212.0016` is **zero** in January, February and March
while TRUAD reports real Google spend in all three ($8,122.88 / $8,250.33 /
$8,244.72), and `66212.0020` carries $8,507.27 / $8,906.00 / $8,466.00 in
exactly those months and nothing afterwards. Same money, two accounts, split
in April.
