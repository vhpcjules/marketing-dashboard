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
