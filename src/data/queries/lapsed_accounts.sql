-- Lapsed accounts: Customers with a real reorder rhythm who have fallen silent.
--
-- An ACCOUNT is a Customer (c.stage LIKE 'CUSTOMER%' AND c.firstorderdate IS
-- NOT NULL - stage alone is insufficient, METHODOLOGY.md "Customer vs Lead"),
-- c.subsidiary = 2 / t.subsidiary = 2 (Versatile High-Performance Coatings,
-- LLC), c.category NOT IN (2, 14).
--
-- Category ids verified 2026-09-05 via BUILTIN.DF(c.category) on the customer
-- table (subsidiary 2, Customers only):
--    2 = Garage Experts   EXCLUDED (franchisees; a different business)
--    3 = DIY              counted
--    4 = Contractor       counted
--    7 = CA Will Call     counted
--   14 = Vendor           EXCLUDED
--
-- An ORDER is a CustInvc or CashSale transaction whose product NET total is
-- positive: SUM(-tl.foreignamount) > 0 per transaction, over the canonical
-- NET line filter (see below). Credit memos and refunds are therefore never
-- "orders"; they only reduce lifetime NET. A $0 or negative invoice is not
-- an order either.
--
-- An account qualifies for the population only with >= 3 orders lifetime, so
-- at least two inter-order gaps exist and a median interval is defined.
--
-- LAPSED as of :as_of (2026-09-05 for the 2026-08 snapshot) means BOTH:
--   1. days_silent = :as_of - MAX(order trandate) > 90, written as
--      MAX(o.td) < :as_of - 90 = TO_DATE('2026-06-07'), i.e. no order in more
--      than 90 days (an order exactly 90 days ago is NOT lapsed); and
--   2. days_silent > 2 * the account's own MEDIAN inter-order gap, where the
--      gap is trandate minus the previous order's trandate for the same
--      customer (LAG ... OVER (PARTITION BY cust ORDER BY td, tid)). Two
--      invoices on the same day are two orders with a 0-day gap; this pulls
--      medians down for accounts that split shipments, which makes the 2x
--      test EASIER to fail, so those accounts lapse sooner under this
--      definition. Recorded, not changed.
--
-- Lifetime NET per account is the canonical NET revenue join copied from
-- net_revenue_monthly.sql, all four transaction types, no date window
-- (NetSuite transaction history for subsidiary 2 begins 2024-10, so
-- "lifetime" means since NetSuite go-live even for the 2023-08 migration
-- cohort):
--   t.type IN (CustInvc, CashSale, CustCred, CustRfnd)  returns deducted
--   tl.mainline = 'F'          excludes the transaction summary line
--   tl.taxline  = 'F'          excludes tax lines
--   tl.item IS NOT NULL        necessary but not sufficient
--   i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--   c.category NOT IN (2, 14)  Garage Experts, Vendor
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or SUM() is NULL, not 0
--
-- Chunking: one query per customer-creation month (or two adjacent months
-- when both are small; c.datecreated half-open ranges, i.e. the cohort key
-- TO_CHAR(c.datecreated,'YYYY-MM'), never first-order month). Everything is
-- aggregated in SQL to one row per lapsed account (the 5000-row cap is never
-- approached: 443 rows in total on 2026-09-05). Tiering, totals, top-N and
-- the category breakdown are done in Python from those rows.
--
-- SuiteQL notes learned here: MEDIAN() and LAG() OVER () both work; a
-- correlated scalar subquery in the SELECT list and TO_CHAR() inside GROUP BY
-- alongside it were rejected ("Invalid or unsupported search"), so lifetime
-- NET is joined in as an inline per-customer aggregate (L) instead. The
-- 2023-08 migration cohort (610 Customers) exceeded the 60 s MCP transport
-- timeout with the L join, so for that chunk QUERY 2 (no L join) was run and
-- lifetime NET was fetched separately with QUERY 3 (t.entity IN (<ids>)).

-- ---------------------------------------------------------------------------
-- QUERY 1: lapsed accounts for one cohort chunk, with lifetime NET.
-- Parameters: :chunk_from / :chunk_to (half-open datecreated range),
--             :silent_cutoff = :as_of - 90, :as_of.
-- ---------------------------------------------------------------------------
SELECT
      o.cust                                                    AS cust,
      COALESCE(c2.companyname, c2.altname, c2.entityid)         AS company,
      c2.category                                               AS cat,
      L.ltv                                                     AS lifetime_net,
      COUNT(o.tid)                                              AS orders,
      MAX(o.td)                                                 AS last_order,
      MEDIAN(o.gap)                                             AS median_gap,
      TO_DATE(:as_of, 'YYYY-MM-DD') - MAX(o.td)                 AS days_silent
FROM (
      SELECT x.cust AS cust, x.tid AS tid, x.td AS td,
             x.td - LAG(x.td) OVER (PARTITION BY x.cust ORDER BY x.td, x.tid) AS gap
      FROM (
            SELECT t.entity AS cust, t.id AS tid, t.trandate AS td
            FROM      transaction     t
            JOIN      transactionline tl ON tl.transaction = t.id
            JOIN      item            i  ON i.id = tl.item
            JOIN      customer        c  ON c.id = t.entity
            WHERE     t.type IN ('CustInvc', 'CashSale')
                  AND t.subsidiary = 2
                  AND tl.mainline  = 'F'
                  AND tl.taxline   = 'F'
                  AND tl.item      IS NOT NULL
                  AND i.itemtype   IS NOT NULL
                  AND c.category   NOT IN (2, 14)
                  AND c.subsidiary = 2
                  AND c.stage LIKE 'CUSTOMER%'
                  AND c.firstorderdate IS NOT NULL
                  AND c.datecreated >= TO_DATE(:chunk_from, 'YYYY-MM-DD')
                  AND c.datecreated <  TO_DATE(:chunk_to,   'YYYY-MM-DD')
            GROUP BY  t.entity, t.id, t.trandate
            HAVING    SUM(-tl.foreignamount) > 0
      ) x
) o
JOIN customer c2 ON c2.id = o.cust
JOIN (
      SELECT t3.entity AS cust, COALESCE(SUM(-tl3.foreignamount), 0) AS ltv
      FROM      transaction     t3
      JOIN      transactionline tl3 ON tl3.transaction = t3.id
      JOIN      item            i3  ON i3.id = tl3.item
      JOIN      customer        c3  ON c3.id = t3.entity
      WHERE     t3.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
            AND t3.subsidiary = 2
            AND tl3.mainline  = 'F'
            AND tl3.taxline   = 'F'
            AND tl3.item      IS NOT NULL
            AND i3.itemtype   IS NOT NULL
            AND c3.category   NOT IN (2, 14)
            AND c3.datecreated >= TO_DATE(:chunk_from, 'YYYY-MM-DD')
            AND c3.datecreated <  TO_DATE(:chunk_to,   'YYYY-MM-DD')
      GROUP BY  t3.entity
) L ON L.cust = o.cust
GROUP BY  o.cust, COALESCE(c2.companyname, c2.altname, c2.entityid), c2.category, L.ltv
HAVING    COUNT(o.tid) >= 3
      AND MAX(o.td) < TO_DATE(:silent_cutoff, 'YYYY-MM-DD')
      AND (TO_DATE(:as_of, 'YYYY-MM-DD') - MAX(o.td)) > 2 * MEDIAN(o.gap)
ORDER BY  4 DESC

-- ---------------------------------------------------------------------------
-- QUERY 2 (fallback for the 2023-08 chunk): QUERY 1 without the L join and
-- without lifetime_net. Identical otherwise.
-- ---------------------------------------------------------------------------
SELECT
      o.cust                                                    AS cust,
      COALESCE(c2.companyname, c2.altname, c2.entityid)         AS company,
      c2.category                                               AS cat,
      COUNT(o.tid)                                              AS orders,
      MAX(o.td)                                                 AS last_order,
      MEDIAN(o.gap)                                             AS median_gap,
      TO_DATE(:as_of, 'YYYY-MM-DD') - MAX(o.td)                 AS days_silent
FROM (
      SELECT x.cust AS cust, x.tid AS tid, x.td AS td,
             x.td - LAG(x.td) OVER (PARTITION BY x.cust ORDER BY x.td, x.tid) AS gap
      FROM (
            SELECT t.entity AS cust, t.id AS tid, t.trandate AS td
            FROM      transaction     t
            JOIN      transactionline tl ON tl.transaction = t.id
            JOIN      item            i  ON i.id = tl.item
            JOIN      customer        c  ON c.id = t.entity
            WHERE     t.type IN ('CustInvc', 'CashSale')
                  AND t.subsidiary = 2
                  AND tl.mainline  = 'F'
                  AND tl.taxline   = 'F'
                  AND tl.item      IS NOT NULL
                  AND i.itemtype   IS NOT NULL
                  AND c.category   NOT IN (2, 14)
                  AND c.subsidiary = 2
                  AND c.stage LIKE 'CUSTOMER%'
                  AND c.firstorderdate IS NOT NULL
                  AND c.datecreated >= TO_DATE(:chunk_from, 'YYYY-MM-DD')
                  AND c.datecreated <  TO_DATE(:chunk_to,   'YYYY-MM-DD')
            GROUP BY  t.entity, t.id, t.trandate
            HAVING    SUM(-tl.foreignamount) > 0
      ) x
) o
JOIN customer c2 ON c2.id = o.cust
GROUP BY  o.cust, COALESCE(c2.companyname, c2.altname, c2.entityid), c2.category
HAVING    COUNT(o.tid) >= 3
      AND MAX(o.td) < TO_DATE(:silent_cutoff, 'YYYY-MM-DD')
      AND (TO_DATE(:as_of, 'YYYY-MM-DD') - MAX(o.td)) > 2 * MEDIAN(o.gap)
ORDER BY  1

-- ---------------------------------------------------------------------------
-- QUERY 3 (companion to QUERY 2): lifetime NET for an explicit id list.
-- ---------------------------------------------------------------------------
SELECT
      t.entity                                      AS cust,
      COALESCE(SUM(-tl.foreignamount), 0)           AS lifetime_net
FROM      transaction     t
JOIN      transactionline tl ON tl.transaction = t.id
JOIN      item            i  ON i.id = tl.item
JOIN      customer        c  ON c.id = t.entity
WHERE     t.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
      AND t.subsidiary = 2
      AND tl.mainline  = 'F'
      AND tl.taxline   = 'F'
      AND tl.item      IS NOT NULL
      AND i.itemtype   IS NOT NULL
      AND c.category   NOT IN (2, 14)
      AND t.entity IN (:ids)
GROUP BY  t.entity
ORDER BY  1
