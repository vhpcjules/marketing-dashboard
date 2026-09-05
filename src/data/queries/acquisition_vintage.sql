-- Acquisition vintage: NET revenue in a 12-month window by the ACTIVE account's
-- acquisition era, where era = TO_CHAR(customer.datecreated, 'YYYY').
--
-- READ THIS FIRST - THE BASIS IS DEFECTIVE FOR LEGACY ACCOUNTS.
-- NetSuite went live in October 2024. Every account that existed in Sage before
-- that was LOADED into NetSuite, and customer.datecreated on those records is
-- the load date, not the original acquisition date. Measured 2026-09-05 on
-- Customers (stage LIKE 'CUSTOMER%', firstorderdate NOT NULL, subsidiary 2,
-- category NOT IN (2,14)):
--
--   datecreated year   customers   what it is
--   2023                 823       migration loads on 2023-08-21 (610), 09-27 (3),
--                                  10-13 (1), 10-30 (209). Sage account numbers
--                                  (entityid) 22..10694 - i.e. ~20 years of accounts.
--   2024                 491       235 more migration loads on 2024-09-10/27/29/30
--                                  PLUS 256 accounts genuinely created after go-live.
--   2025               1,120       genuine
--   2026                 609       genuine
--
-- No record carries the original date. Checked 2026-09-05: all 108 ENTITY-type
-- custom fields (customfield table) - none is a date of creation / Sage date /
-- legacy date; customer.firstorderdate, firstsaledate, startdate, firstvisit,
-- dateclosed, datelead, dateprospect, dateconversion are all 2023+ for migrated
-- accounts (exactly one record has firstorderdate 2002); comments never mention
-- 'since' or 'sage'; the transaction table holds no pre-go-live history
-- (12 revenue-type transactions dated 2023, 5,481 in 2024, none earlier).
-- The v1 (Aug 18 2026) vintage bands used Sage created dates from a source
-- outside NetSuite that is not in this repository. Under this query the
-- 2007-2021 bands are therefore EMPTY and every legacy account lands in
-- 2022-2023 (or 2024). The pre-2018 spot-checks are not reproducible here.
--
-- Population: every account (customer.id) with at least one NET-positive
-- transaction in the window (pos_txns > 0), on the VHPC subsidiary, excluding
-- categories 2 and 14. No stage/firstorderdate filter: the brief defines the
-- population by activity. (2 of 2,047 transacting 2025 accounts are stage LEAD.)
--
-- Category mapping verified with BUILTIN.DF(customer.category) 2026-09-05:
--   2 = Garage Experts (excluded), 3 = DIY, 4 = Contractor, 7 = CA Will Call
--   (kept), 14 = Vendor (excluded).
--
-- Layers (SuiteQL has no CTEs, so they nest):
--   x  one row per (customer, transaction): transaction-level NET, using the
--      canonical join from net_revenue_monthly.sql verbatim:
--        t.type IN (4)              all four types or returns are silently ignored
--        t.subsidiary = 2           Versatile High-Performance Coatings, LLC
--        tl.mainline = 'F'          excludes the transaction summary line
--        tl.taxline  = 'F'          excludes tax lines
--        tl.item IS NOT NULL        necessary but not sufficient
--        i.itemtype IS NOT NULL     CRITICAL: freight/shipping items have NULL itemtype
--        c.category NOT IN (2, 14)  Garage Experts, Vendor
--        SUM(-tl.foreignamount)     the sign flip is required
--        COALESCE                   or SUM() is NULL, not 0
--   y  one row per customer: window NET and the count of NET-positive transactions
--   outer  one row per (datecreated year, firstorderdate year):
--        acct_anypos / net_anypos   ACTIVE accounts (>= 1 NET-positive transaction)
--                                   and their NET - THE PUBLISHED BASIS
--        acct_yearpos / net_yearpos alternative reading: window NET > 0
--        acct_all / net_all         every transacting account incl. credit-only
--      firstorderdate year is carried as a secondary lens only; for migrated
--      accounts it is the first NetSuite-era order, not the first order ever.
--
-- Run ONCE PER datecreated-year slice (:dc_from / :dc_to) per window
-- (:date_from / :date_to): a 12-month transaction join across all customers
-- risks the 180 s SuiteQL timeout; a one-year slice of customers runs in well
-- under it and returns < 10 rows. Bands (2007-2009 ... 2026) are formed from
-- the datecreated year in Python; only aggregates reach the snapshot.
--
-- Windows used for the 2026-08 snapshot:
--   fy2025       :date_from 2025-01-01  :date_to 2026-01-01
--   ttm_2026_08  :date_from 2025-09-01  :date_to 2026-09-01
-- Slices: dc < 2024-01-01 | 2024 | 2025 | >= 2026-01-01
SELECT    y.dc_yr                                                          AS dc_yr,
          y.fo_yr                                                          AS fo_yr,
          COUNT(CASE WHEN y.pos_txns > 0 THEN 1 END)                       AS acct_anypos,
          COALESCE(SUM(CASE WHEN y.pos_txns > 0 THEN y.net ELSE 0 END), 0) AS net_anypos,
          COUNT(CASE WHEN y.net > 0 THEN 1 END)                            AS acct_yearpos,
          COALESCE(SUM(CASE WHEN y.net > 0 THEN y.net ELSE 0 END), 0)      AS net_yearpos,
          COUNT(y.cid)                                                     AS acct_all,
          COALESCE(SUM(y.net), 0)                                          AS net_all
FROM (
    SELECT x.cid                                     AS cid,
           x.dc_yr                                   AS dc_yr,
           x.fo_yr                                   AS fo_yr,
           COALESCE(SUM(x.net), 0)                   AS net,
           COUNT(CASE WHEN x.net > 0 THEN 1 END)     AS pos_txns
    FROM (
        SELECT c.id                                  AS cid,
               TO_CHAR(c.datecreated, 'YYYY')        AS dc_yr,
               TO_CHAR(c.firstorderdate, 'YYYY')     AS fo_yr,
               t.id                                  AS tid,
               COALESCE(SUM(-tl.foreignamount), 0)   AS net
        FROM      transaction     t
        JOIN      transactionline tl ON tl.transaction = t.id
        JOIN      item            i  ON i.id = tl.item
        JOIN      customer        c  ON c.id = t.entity
        WHERE     t.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
              AND t.subsidiary  = 2
              AND tl.mainline   = 'F'
              AND tl.taxline    = 'F'
              AND tl.item       IS NOT NULL
              AND i.itemtype    IS NOT NULL
              AND c.category    NOT IN (2, 14)
              AND c.datecreated >= TO_DATE(:dc_from,   'YYYY-MM-DD')
              AND c.datecreated <  TO_DATE(:dc_to,     'YYYY-MM-DD')
              AND t.trandate    >= TO_DATE(:date_from, 'YYYY-MM-DD')
              AND t.trandate    <  TO_DATE(:date_to,   'YYYY-MM-DD')
        GROUP BY  c.id, TO_CHAR(c.datecreated, 'YYYY'), TO_CHAR(c.firstorderdate, 'YYYY'), t.id
    ) x
    GROUP BY x.cid, x.dc_yr, x.fo_yr
) y
GROUP BY  y.dc_yr, y.fo_yr
ORDER BY  1, 2

-- Companion diagnostics run 2026-09-05 (customer table only, no transaction join):
--
-- SELECT TO_CHAR(c.datecreated,'YYYY-MM-DD') AS d, COUNT(c.id) AS customers,
--        MIN(c.entitynumber) AS min_no, MAX(c.entitynumber) AS max_no
-- FROM customer c
-- WHERE c.subsidiary = 2 AND c.stage LIKE 'CUSTOMER%' AND c.firstorderdate IS NOT NULL
--   AND c.category NOT IN (2,14) AND c.datecreated < TO_DATE('2024-10-01','YYYY-MM-DD')
-- GROUP BY TO_CHAR(c.datecreated,'YYYY-MM-DD') ORDER BY 1
--   -> 1,058 pre-go-live accounts on 8 load dates (see header table).
--
-- SELECT scriptid, name FROM customfield WHERE fieldtype = 'ENTITY' ORDER BY scriptid
--   -> 108 fields, none a legacy creation date.
