-- Month-one ("M1") NET revenue for ONE acquisition cohort.
--
-- Cohort  = the customer record's creation month, [:cohort_from, :cohort_to),
--           never first-order month (METHODOLOGY.md, "Cohorts").
-- M1      = transactions dated in the SAME calendar month as the record's
--           creation. Written as a month-bounded range on t.trandate rather
--           than TO_CHAR(trandate) = TO_CHAR(datecreated) so the planner can
--           prune; the two are equivalent because both bounds are the cohort
--           month. Month-bounded means a back-dated invoice earlier in the
--           month than the record itself IS counted - see the note in
--           geography_12mo.sql on why M1 can exceed first-90-days.
--
-- customers_m1 = COUNT(DISTINCT c.id) with at least one qualifying line in
-- the window. This is the PUBLISHED definition: the frozen cohorts_m1.json
-- files carry it (2026-01 = 44, which is also customers_m1_live in the
-- cohorts_m13 file for that month), and drift detection compares a fresh
-- pull against those frozen values, so the definition must not move. It is
-- NOT the cohort's record count (lead_quality.customers, 55 for 2026-01):
-- a customer created in January whose first order lands in February has
-- zero M1 revenue by construction and is not in this count. METHODOLOGY.md
-- carries that as a known diagnostic; the narrative says "at least".
--
-- This is the companion query documented at the foot of cohorts_m13.sql,
-- with the canonical NET revenue join from net_revenue_monthly.sql. Every
-- clause is load-bearing; the reasons are in those two files and are not
-- repeated here so they cannot drift apart:
--
--   t.type IN (4)              CustInvc, CashSale, CustCred, CustRfnd
--   t.subsidiary = 2           Versatile High-Performance Coatings, LLC
--   tl.mainline = 'F'          no summary line
--   tl.taxline  = 'F'          no tax lines
--   tl.item IS NOT NULL        necessary, not sufficient
--   i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--   c.category NOT IN (2, 14)  2 = Garage Experts (franchisees), 14 = Vendor
--   c.subsidiary = 2           customer record also on the VHPC subsidiary
--   c.stage LIKE 'CUSTOMER%'   a Customer...
--   c.firstorderdate NOT NULL  ...that has actually ordered
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or an empty cohort returns NULL, not 0
--
-- Run ONE cohort month per call: transaction joins hit the 180 s SuiteQL
-- timeout on multi-month ranges. Always returns exactly one row.
SELECT
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
