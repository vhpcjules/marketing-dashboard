-- Retention: reorder rate by first-order size band, and time to second order.
--
-- Population: Customers (METHODOLOGY.md "Customer vs Lead") on the VHPC
-- subsidiary, excluding categories 2 and 14, whose record was CREATED in
-- 2025-01..2026-05 - so every customer in the window has had at least 90 days.
-- Cohort membership is TO_CHAR(c.datecreated,'YYYY-MM'), written here as a
-- half-open datecreated range and run ONE creation month per call: the
-- transaction join hits the 180 s SuiteQL timeout on multi-month ranges.
--
-- WHAT "FIRST ORDER" MEANS HERE, AND WHY IT IS NOT customer.firstorderdate.
-- The brief defines first-order NET as the NET revenue dated firstorderdate.
-- On this data that definition is defective: firstorderdate is the ORDER
-- date (the sales order), while NET revenue posts on the invoice / cash sale,
-- which is typically dated the next business day. Measured live 2026-09-05
-- over the whole window, 688 of 1,480 customers (46.5%) have $0 NET dated
-- firstorderdate; 576 of them post their first invoice within 2 days of it,
-- 111 within 3-17 days. Under the literal definition every one of those first
-- invoices is a "reorder" and every one of those customers is "under $400".
-- So:
--
--   first order date (fpd)  = the customer's earliest NET-positive
--                             transaction date (analytic MIN ... OVER
--                             (PARTITION BY customer)). This is the day the
--                             first order's revenue actually posted.
--   first_order_net         = NET revenue of all lines dated fpd.
--   band                    = <400 / 400-2499 / >=2500 on first_order_net.
--   reordered               = has a NET-positive transaction (transaction-
--                             level net > 0, so credits and refunds never
--                             count) dated strictly after fpd.
--   gap_days                = first such date minus fpd; NULL = one-and-done.
--
-- Output is a histogram (band x gap_days -> customers), never per-customer
-- rows. Reorder rates, the median and the by-day shares are formed from the
-- histogram in Python; only aggregates reach the snapshot.
--
-- Everything inside the innermost subquery is the canonical NET revenue join
-- copied from net_revenue_monthly.sql, plus the Customer definition:
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
--   c.datecreated range        the cohort month, half-open
--   SUM(-tl.foreignamount)     sign flip is required
--   COALESCE                   or SUM() is NULL, not 0
--
-- Layers (SuiteQL has no CTEs, so they nest):
--   x  one row per (customer, transaction): transaction-level NET
--   y  x plus fpd, the customer's first NET-positive transaction date
--   p  one row per customer: band and gap_days
--   outer  histogram: band x gap_days -> customers
SELECT    p.band                                        AS band,
          p.gap_days                                    AS gap_days,
          COUNT(p.cid)                                  AS customers
FROM (
    SELECT y.cid                                        AS cid,
           y.fpd                                        AS fpd,
           CASE WHEN COALESCE(SUM(CASE WHEN y.td = y.fpd THEN y.net ELSE 0 END), 0) <  400  THEN 'under_400'
                WHEN COALESCE(SUM(CASE WHEN y.td = y.fpd THEN y.net ELSE 0 END), 0) <  2500 THEN '400_2499'
                ELSE '2500_plus' END                    AS band,
           MIN(CASE WHEN y.td > y.fpd AND y.net > 0 THEN y.td END) - y.fpd
                                                        AS gap_days
    FROM (
        SELECT x.cid AS cid, x.tid AS tid, x.td AS td, x.net AS net,
               MIN(CASE WHEN x.net > 0 THEN x.td END) OVER (PARTITION BY x.cid) AS fpd
        FROM (
            SELECT c.id                                 AS cid,
                   t.id                                 AS tid,
                   t.trandate                           AS td,
                   COALESCE(SUM(-tl.foreignamount), 0)  AS net
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
            GROUP BY  c.id, t.id, t.trandate
        ) x
    ) y
    GROUP BY y.cid, y.fpd
) p
GROUP BY  p.band, p.gap_days
ORDER BY  1, 2

-- Companion (customer table only, no transaction join, one call for the
-- whole window): the denominator check. Its per-month counts must equal the
-- per-month histogram totals, which proves every customer in the window has
-- at least one qualifying NET line (2026-09-05: 1,480 = 1,480).
--
-- SELECT TO_CHAR(c.datecreated,'YYYY-MM') AS cohort, COUNT(c.id) AS customers
-- FROM customer c
-- WHERE c.subsidiary = 2 AND c.stage LIKE 'CUSTOMER%' AND c.firstorderdate IS NOT NULL
--   AND c.category NOT IN (2,14)
--   AND c.datecreated >= TO_DATE('2025-01-01','YYYY-MM-DD')
--   AND c.datecreated <  TO_DATE('2026-06-01','YYYY-MM-DD')
-- GROUP BY TO_CHAR(c.datecreated,'YYYY-MM') ORDER BY 1
