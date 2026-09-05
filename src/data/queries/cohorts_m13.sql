-- First-90-days ("M1-3") NET revenue by acquisition cohort.
--
-- Cohort  = TO_CHAR(customer.datecreated, 'YYYY-MM'), the record-creation
--           month. Never first-order month (METHODOLOGY.md, "Cohorts").
-- Window  = the 90 calendar days starting on the customer's creation DAY:
--           trandate >= TRUNC(datecreated) AND trandate < TRUNC(datecreated) + 90
--
-- WHY TRUNC. customer.datecreated is a timestamp. Written as
--   trandate >= datecreated
-- (which is how the methodology table and v1 spelled it) a cash sale dated
-- the creation day compares midnight against, say, 14:32 on the same day and
-- is EXCLUDED. Because records are created at order time, that drops most
-- customers' first order: 2025-01 has 55 customers with M1 revenue but only 41
-- with "M1-3" revenue under the timestamp form - a window that is supposed to
-- contain M1 cannot contain fewer customers than M1. TRUNC makes the window
-- what the label says: day 0 through day 89 inclusive. Verified 2026-09-05:
-- the timestamp form reproduces v1's published matched-cohort totals to the
-- dollar (Jan-Apr 2025 460,932 / 237 customers; Jan-Apr 2026 310,602 / 192),
-- so the published figures carried this defect. Not drift - a definition bug.
--
-- Everything below the window is the canonical NET revenue join copied from
-- net_revenue_monthly.sql, plus the Customer definition:
--
--   t.type IN (4)              all four types or returns are silently ignored
--   t.subsidiary = 2           Versatile High-Performance Coatings, LLC
--   tl.mainline = 'F'          excludes the transaction summary line
--   tl.taxline  = 'F'          excludes tax lines
--   tl.item IS NOT NULL        necessary but not sufficient
--   i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--   c.category NOT IN (2, 14)  2 = Garage Experts (franchisees), 14 = Vendor.
--                              Verified by BUILTIN.DF on 2026-09-05:
--                              3 = DIY, 4 = Contractor, 7 = CA Will Call (kept).
--   c.subsidiary = 2           customer record also on the VHPC subsidiary
--   c.stage LIKE 'CUSTOMER%'   a Customer, not a Lead/Prospect...
--   c.firstorderdate NOT NULL  ...that has actually ordered
--   c.datecreated range        the cohort month, as a half-open date range
--                              (equivalent to TO_CHAR(datecreated,'YYYY-MM'))
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or SUM() is NULL, not 0, for an empty cohort
--
-- customers_m13 = COUNT(DISTINCT c.id): customers with at least one qualifying
-- line in the window (a customer whose window nets to zero still counts).
--
-- Run ONE cohort month per call: transaction joins hit the 180 s SuiteQL
-- timeout on multi-month ranges. Only cohorts whose window has closed
-- (month_end + 90 days <= as_of; src.periods.m13_closed) may be written.
SELECT
      COUNT(DISTINCT c.id)                          AS customers_m13,
      COALESCE(SUM(-tl.foreignamount), 0)           AS m13_net_revenue,
      COUNT(DISTINCT t.id)                          AS transactions
FROM      transaction     t
JOIN      transactionline tl ON tl.transaction = t.id
JOIN      item            i  ON i.id = tl.item
JOIN      customer        c  ON c.id = t.entity
WHERE     t.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
      AND t.subsidiary     = 2
      AND tl.mainline      = 'F'
      AND tl.taxline       = 'F'
      AND tl.item          IS NOT NULL
      AND i.itemtype       IS NOT NULL
      AND c.category       NOT IN (2, 14)
      AND c.subsidiary     = 2
      AND c.stage          LIKE 'CUSTOMER%'
      AND c.firstorderdate IS NOT NULL
      AND c.datecreated   >= TO_DATE(:cohort_from, 'YYYY-MM-DD')
      AND c.datecreated    < TO_DATE(:cohort_to,   'YYYY-MM-DD')
      AND t.trandate      >= TRUNC(c.datecreated)
      AND t.trandate       < TRUNC(c.datecreated) + 90

-- Companion query, same join, M1 condition. Written to the snapshot as
-- m1_net_revenue_live so the M1-3 / M1 multiple can be formed from one file.
-- Live on purpose: the frozen M1 lives in cohorts_m1.json, never here.
--
-- SELECT COUNT(DISTINCT c.id) AS customers_m1,
--        COALESCE(SUM(-tl.foreignamount), 0) AS m1_net_revenue
-- FROM   <same join and WHERE as above, minus the two trandate lines>
--    AND TO_CHAR(t.trandate, 'YYYY-MM') = TO_CHAR(c.datecreated, 'YYYY-MM')
