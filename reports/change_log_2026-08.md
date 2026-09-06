# Change log — August 2026

Built 2026-09-06. Each row compares August 2026 with July 2026. Change is the relative percent change via `src.units.delta` — never a point difference, even for rates. The threshold is 2 standard deviations of the trailing 12 month-over-month changes where at least 6 exist, or a configured per-metric value; a move beyond it is flagged for a human to read before publishing.

Build notes:

- gmb: GMB figures for August 2026 have not been supplied yet. Add gmb_2026-08.json or gmb_2026-08.csv under data/manual/2026/ and rebuild.
- hotjar: HOTJAR figures for August 2026 have not been supplied yet. Add hotjar_2026-08.json or hotjar_2026-08.csv under data/manual/2026/ and rebuild.

| Metric | Source | Jul 26 | Aug 26 | Change | Direction | Threshold | Exceeds |
|---|---|---|---|---|---|---|---|
| New customers (M1 basis) | cohorts_m1 | 72 | 82 | +13.9% | ↑ better | ±46.9% (2 SD of trailing 12 months) | no |
| Month-one NET revenue | cohorts_m1 | $51,088.00 | $85,123.40 | +66.6% | ↑ better | ±76.4% (2 SD of trailing 12 months) | no |
| Average first order | cohorts_m1 | $709.56 | $1,038.09 | +46.3% | ↑ better | ±58.2% (2 SD of trailing 12 months) | no |
| Marketing spend (true operating) | marketing_spend | $10,003.95 | $8,489.18 | -15.1% | ↓ better | ±88.4% (2 SD of trailing 12 months) | no |
| Lead records created | lead_quality | 276 | 317 | +14.9% | ↑ better | ±56.6% (2 SD of trailing 12 months) | no |
| Phone capture rate | lead_quality | 56.9% | 61.5% | +8.1% | ↑ better | ±36.2% (2 SD of trailing 12 months) | no |
| Email capture rate | lead_quality | 97.8% | 98.4% | +0.6% | ↑ better | ±1.8% (2 SD of trailing 12 months) | no |
| Lead-to-customer conversion (to date) | lead_quality | 28.6% | 27.4% | -4.2% | ↓ worse | ±35.4% (2 SD of trailing 12 months) | no |

Flagged: none.
