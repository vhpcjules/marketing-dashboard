-- Per-account NET revenue in a window, with the keys needed to date the
-- account's acquisition from Sage.
--
-- NetSuite cannot date legacy accounts: customer.datecreated is the MIGRATION
-- date for everything that came over from Sage in Q4 2024 (see
-- acquisition_vintage.json -> coverage_note). Jules supplied the Sage
-- "Customer Sales History by Period" reports for 2019-2024
-- (data/manual/sage/customer_sales_history_2019_2024.json). The join is done
-- OFFLINE in src/data/vintage.py: the Sage customer number is expected to be
-- the first token of the NetSuite entityid ('0000004 Artistic Concrete'), so
-- this query returns entityid verbatim plus the NetSuite creation year as the
-- fallback for accounts Sage never saw.
--
-- Same canonical NET join as cohorts_m1.sql; clauses explained there. No
-- c.stage / c.firstorderdate filter, matching revenue_total_monthly.sql, so
-- the band totals sum to the company total for the same window.
--
-- One row per account with at least one qualifying line in the window.
-- Roughly two thousand rows for a year; well under the SuiteQL cap.
SELECT
      c.id                                          AS customer_id,
      c.entityid                                    AS entityid,
      TO_CHAR(c.datecreated, 'YYYY')                AS datecreated_year,
      TO_CHAR(c.firstorderdate, 'YYYY')             AS firstorder_year,
      COALESCE(SUM(-tl.foreignamount), 0)           AS net_revenue,
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
GROUP BY  c.id, c.entityid, TO_CHAR(c.datecreated, 'YYYY'), TO_CHAR(c.firstorderdate, 'YYYY')
