-- Customer mix by acquisition cohort: new Customers by category, with their
-- M1 and first-90-days ("M1-3") NET revenue by category.
--
-- Three queries, all run ONE cohort month per call (transaction joins hit the
-- 180 s SuiteQL timeout on multi-month ranges; the customer-only count query
-- is cheap and may be run for the whole range). Aggregated in SQL - never
-- pull raw rows (5000-row cap).
--
-- Category mapping, verified against the customercategory table 2026-09-05:
--   3 = DIY            counted
--   4 = Contractor     counted
--   7 = CA Will Call   counted if present (no Customer created in any cohort
--                      2025-06..2026-08 carries it; v1 showed 1 because its
--                      12-month window was keyed on a different date)
--   2 = Garage Experts EXCLUDED (franchisees; a different business)
--  14 = Vendor         EXCLUDED
-- Other ids (1 Franchise, 5 Architect, 6 Designer, 8 Drop Off, 9 Facility
-- Manager, 11 Drop Ship, 12 Engineer, 13 School) had no Customer records in
-- the window; the queries group by the raw id so any that appear are kept and
-- named by a follow-up lookup on customercategory (BUILTIN.DF inside
-- GROUP BY is rejected by SuiteQL for this table).
--
-- Cohort  = TO_CHAR(customer.datecreated, 'YYYY-MM'): the record-creation
--           month, expressed as a half-open date range on datecreated (same
--           thing, index-friendly). NEVER first-order month.
-- Customer= c.stage LIKE 'CUSTOMER%' AND c.firstorderdate IS NOT NULL. Stage
--           alone is insufficient (METHODOLOGY.md "Customer vs Lead").
-- c.subsidiary = 2 / t.subsidiary = 2 : Versatile High-Performance Coatings, LLC.
--
-- Everything in the revenue queries is the canonical NET revenue join copied
-- from net_revenue_monthly.sql:
--   t.type IN (4)              all four types or returns are silently ignored
--   tl.mainline = 'F'          excludes the transaction summary line
--   tl.taxline  = 'F'          excludes tax lines
--   tl.item IS NOT NULL        necessary but not sufficient
--   i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--   c.category NOT IN (2, 14)  Garage Experts, Vendor
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or SUM() is NULL, not 0, for an empty group
--
-- M1 window   : t.trandate in the cohort's own calendar month, i.e.
--               TO_CHAR(t.trandate,'YYYY-MM') = TO_CHAR(c.datecreated,'YYYY-MM'),
--               written as a half-open trandate range on the cohort month.
-- M1-3 window : t.trandate >= TRUNC(c.datecreated) AND
--               t.trandate <  TRUNC(c.datecreated) + 90
--               (day 0 through day 89 of the creation DAY). This is the basis
--               used by cohorts_m13.sql; see that file for why TRUNC matters:
--               datecreated is a timestamp and the un-TRUNCed form drops
--               same-day orders placed after the record's creation time.
--               The un-TRUNCed form (t.trandate >= c.datecreated) is still
--               computed alongside as m13_net_revenue_v1_timestamp_basis
--               because the Aug 18 2026 build's M1-3/M1 multiples were on it.
--               Only cohorts whose window has closed (month_end + 90 days <=
--               as_of; src.periods.m13_closed) get an M1-3 figure; others null.
--
-- customers      = COUNT(c.id) in query 1: every Customer created in the month.
-- customers_m1   = COUNT(DISTINCT c.id) in query 2: those with an M1 line.
-- customers_m13  = COUNT(DISTINCT c.id) in query 3: those with a line in the window.

-- ---------------------------------------------------------------------------
-- Query 1: new Customers per cohort month by category (customer table only)
-- ---------------------------------------------------------------------------
SELECT
      TO_CHAR(c.datecreated, 'YYYY-MM')             AS cohort,
      c.category                                    AS category_id,
      COUNT(c.id)                                   AS customers
FROM      customer c
WHERE     c.subsidiary     = 2
      AND c.stage          LIKE 'CUSTOMER%'
      AND c.firstorderdate IS NOT NULL
      AND c.category       NOT IN (2, 14)
      AND c.datecreated   >= TO_DATE(:range_from, 'YYYY-MM-DD')
      AND c.datecreated    < TO_DATE(:range_to,   'YYYY-MM-DD')
GROUP BY  TO_CHAR(c.datecreated, 'YYYY-MM'), c.category
ORDER BY  1, 2

-- ---------------------------------------------------------------------------
-- Query 2: M1 NET revenue by category, one cohort month per call
-- ---------------------------------------------------------------------------
SELECT
      c.category                                    AS category_id,
      COUNT(DISTINCT c.id)                          AS customers_m1,
      COALESCE(SUM(-tl.foreignamount), 0)           AS m1_net_revenue,
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
      AND t.trandate      >= TO_DATE(:cohort_from, 'YYYY-MM-DD')
      AND t.trandate       < TO_DATE(:cohort_to,   'YYYY-MM-DD')
GROUP BY  c.category
ORDER BY  1

-- ---------------------------------------------------------------------------
-- Query 3: first-90-days NET revenue by category, one CLOSED cohort per call
-- ---------------------------------------------------------------------------
SELECT
      c.category                                    AS category_id,
      COUNT(DISTINCT c.id)                          AS customers_m13,
      COALESCE(SUM(-tl.foreignamount), 0)           AS m13_net_revenue,
      COALESCE(SUM(CASE WHEN t.trandate >= c.datecreated
                        THEN -tl.foreignamount ELSE 0 END), 0)
                                                    AS m13_net_revenue_ts_basis,
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
GROUP BY  c.category
ORDER BY  1
