-- Source mix by acquisition cohort: new Customers and ALL records created,
-- by NetSuite lead source, with the Customers' M1 NET revenue by source.
--
-- Three queries. The two customer-table-only counts run for the whole
-- 12-month range in one call each (no transaction join, well under the
-- 180 s SuiteQL timeout, ~105-140 rows, far under the 5000-row cap). The M1
-- revenue query joins transactions and is run ONE cohort month per call.
-- Everything is aggregated in SQL - raw rows are never pulled.
--
-- Lead source = customer.leadsource, a reference to the campaign table.
-- Grouped by the raw id; names come from BUILTIN.DF(c.leadsource) in a
-- separate lookup (query 0) so the revenue query never carries a DF inside
-- GROUP BY. NULL leadsource is reported as the source 'Untracked'. Values
-- present in 2025-09..2026-08 (id -> BUILTIN.DF name):
--   NULL  Untracked                     86776 CAM45 Meta
--   1371  CAM37 Organic Search          86774 CAM43 Paid Search
--   1369  CAM35 Direct                  86775 CAM44 PMAX
--   361   CAM3 Website Newsletter Sign-ups  -5  Trade Show
--   947   CAM15 Web Referral            -4    Partner Referral
--   948   CAM16 Phone Call              899   CAM9 MailChimp Email
--   905   CAM12 Concrete Network        -6    Web
--   949   CAM17 Specified Job           946   CAM14 Google Search
--   1368  CAM34 Organic Social          1370  CAM36 Organic Shopping
--   945   CAM13 Personal Social         896   CAM6 Concrete Decor
--   1404  CAM38 Test Campaign           -3    Other
-- (negative ids are NetSuite's built-in lead sources; positive ids are
-- campaign records.)
--
-- Cohort   = TO_CHAR(customer.datecreated, 'YYYY-MM'): the record-creation
--            month, expressed as a half-open date range on datecreated (same
--            thing, index-friendly; datecreated carries a time component so
--            the bounds and the TO_CHAR grouping agree at month edges).
--            NEVER first-order month.
-- Customer = c.stage LIKE 'CUSTOMER%' AND c.firstorderdate IS NOT NULL. Stage
--            alone is insufficient (METHODOLOGY.md "Customer vs Lead").
-- Record   = any customer-table row created in the month (leads, prospects,
--            customers) under the same subsidiary/category filters. Used for
--            the untracked share of everything that entered the CRM.
-- c.subsidiary = 2 / t.subsidiary = 2 : Versatile High-Performance Coatings, LLC.
-- c.category NOT IN (2, 14) : 2 = Garage Experts (franchisees; a different
--            business), 14 = Vendor. Verified with BUILTIN.DF(c.category) on
--            2026-09-05: 3 = DIY, 4 = Contractor are the two real customer
--            categories; 7 = CA Will Call and 9 = Facility Manager also exist
--            and are kept. NOT IN also drops the 2 NULL-category records,
--            matching net_revenue_monthly.sql.
--
-- Query 3 is the canonical NET revenue join copied from net_revenue_monthly.sql:
--   t.type IN (4)              all four types or returns are silently ignored
--   tl.mainline = 'F'          excludes the transaction summary line
--   tl.taxline  = 'F'          excludes tax lines
--   tl.item IS NOT NULL        necessary but not sufficient
--   i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--   c.category NOT IN (2, 14)  Garage Experts, Vendor
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or SUM() is NULL, not 0, for an empty group
-- M1 window : t.trandate in the cohort's own calendar month, i.e.
--             TO_CHAR(t.trandate,'YYYY-MM') = TO_CHAR(c.datecreated,'YYYY-MM'),
--             written as a half-open trandate range on the cohort month.
--
-- customers     = COUNT(c.id) in query 1: every Customer created in the month.
-- records       = COUNT(c.id) in query 2: every record created in the month.
-- customers_m1  = COUNT(DISTINCT c.id) in query 3: Customers with an M1 line.
-- COUNT(field), not SUM(CASE ...): house rule.

-- ---------------------------------------------------------------------------
-- Query 0: lead source id -> name lookup (tiny; run once)
-- ---------------------------------------------------------------------------
SELECT
      c.leadsource                                  AS leadsource_id,
      BUILTIN.DF(c.leadsource)                      AS leadsource_name,
      COUNT(c.id)                                   AS records,
      COUNT(c.firstorderdate)                       AS with_first_order
FROM      customer c
WHERE     c.subsidiary   = 2
      AND c.category     NOT IN (2, 14)
      AND c.datecreated >= TO_DATE(:range_from, 'YYYY-MM-DD')
      AND c.datecreated  < TO_DATE(:range_to,   'YYYY-MM-DD')
GROUP BY  c.leadsource, BUILTIN.DF(c.leadsource)
ORDER BY  3 DESC

-- ---------------------------------------------------------------------------
-- Query 1: new Customers per cohort month by lead source (customer table only)
-- ---------------------------------------------------------------------------
SELECT
      TO_CHAR(c.datecreated, 'YYYY-MM')             AS cohort,
      c.leadsource                                  AS leadsource_id,
      COUNT(c.id)                                   AS customers
FROM      customer c
WHERE     c.subsidiary     = 2
      AND c.stage          LIKE 'CUSTOMER%'
      AND c.firstorderdate IS NOT NULL
      AND c.category       NOT IN (2, 14)
      AND c.datecreated   >= TO_DATE(:range_from, 'YYYY-MM-DD')
      AND c.datecreated    < TO_DATE(:range_to,   'YYYY-MM-DD')
GROUP BY  TO_CHAR(c.datecreated, 'YYYY-MM'), c.leadsource
ORDER BY  1, 2

-- ---------------------------------------------------------------------------
-- Query 2: ALL records created per cohort month by lead source
-- ---------------------------------------------------------------------------
SELECT
      TO_CHAR(c.datecreated, 'YYYY-MM')             AS cohort,
      c.leadsource                                  AS leadsource_id,
      COUNT(c.id)                                   AS records
FROM      customer c
WHERE     c.subsidiary     = 2
      AND c.category       NOT IN (2, 14)
      AND c.datecreated   >= TO_DATE(:range_from, 'YYYY-MM-DD')
      AND c.datecreated    < TO_DATE(:range_to,   'YYYY-MM-DD')
GROUP BY  TO_CHAR(c.datecreated, 'YYYY-MM'), c.leadsource
ORDER BY  1, 2

-- ---------------------------------------------------------------------------
-- Query 3: M1 NET revenue by lead source, one cohort month per call
-- ---------------------------------------------------------------------------
SELECT
      c.leadsource                                  AS leadsource_id,
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
GROUP BY  c.leadsource
ORDER BY  1
