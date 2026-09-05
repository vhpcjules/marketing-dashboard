-- Total NET revenue for ONE calendar month, all customers.
--
-- The company target (set by leadership; recorded in
-- data/manual/<year>/approved_marketing_budget.json -> targets) is 19% growth
-- in TOTAL NET revenue over the prior year, not in new-customer revenue. This
-- is the series that target is paced on. Marketing's own frame (M1 and
-- revenue to date by acquisition cohort) is a SUBSET of this figure, formed by
-- the same join with a cohort restriction on c.datecreated, so the two can be
-- compared without a basis mismatch.
--
-- Same canonical NET join as cohorts_m1.sql / net_revenue_monthly.sql; every
-- clause is load-bearing and explained there:
--
--   t.type IN (4)              CustInvc, CashSale, CustCred, CustRfnd
--   t.subsidiary = 2           Versatile High-Performance Coatings, LLC
--   tl.mainline = 'F'          no summary line
--   tl.taxline  = 'F'          no tax lines
--   tl.item IS NOT NULL        necessary, not sufficient
--   i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--   c.category NOT IN (2, 14)  2 = Garage Experts (franchisees), 14 = Vendor
--   c.subsidiary = 2           customer record also on the VHPC subsidiary
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or an empty month returns NULL, not 0
--
-- No c.stage / c.firstorderdate filter: a refund to a lapsed account is still
-- company revenue (negative). Run ONE month per call. Exactly one row.
SELECT
      COALESCE(SUM(-tl.foreignamount), 0)           AS net_revenue,
      COUNT(DISTINCT c.id)                          AS customers_transacting,
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
      AND t.trandate      >= TO_DATE(:date_from, 'YYYY-MM-DD')
      AND t.trandate       < TO_DATE(:date_to,   'YYYY-MM-DD')
