-- Geography: new customers by ship-to STATE for the rolling 12 calendar-closed
-- cohort months ending with the reporting month, plus M1 NET revenue and
-- first-90-days NET revenue by state.
--
-- Three statements. The first is customer-table-only and runs for the whole
-- window in one call. The second and third join transactions and MUST be run
-- ONE COHORT MONTH AT A TIME (:cohort_from / :cohort_to are the first day of
-- the cohort month and the first day of the next month) or they hit the
-- 180-second SuiteQL timeout. Results are aggregated by state in SQL, so each
-- call returns ~50 rows, far under the 5000-row cap. Ingest sums the monthly
-- rows per state in Python and writes ONE snapshot:
-- data/snapshots/<reporting month>/geography_12mo.json.
--
-- ADDRESS SOURCE (discovered 2026-09-05). customer.shipstate / billstate are
-- NOT exposed to SuiteQL ("NOT_EXPOSED - Not available for channel SEARCH").
-- The state comes from the customer's DEFAULT SHIPPING address book entry:
--
--   customeraddressbook cab       ON cab.entity = c.id
--                                 AND cab.defaultshipping = 'T'
--   customeraddressbookentityaddress ea ON ea.nkey = cab.addressbookaddress
--   ea.state                      2-letter code (US states + a few Canadian
--                                 provinces: ON, MB, AB). Not normalised; no
--                                 full-name variants were found.
--
-- Both joins are LEFT joins so a customer with no default shipping address is
-- still counted (state NULL -> reported as customers_with_no_state). Verified
-- the join does not duplicate: COUNT(c.id) with and without the address joins
-- is identical (999 for 2025-09..2026-08), i.e. at most one defaultshipping
-- row per customer.
--
-- Every other clause is load-bearing:
--
--   c.subsidiary = 2 / t.subsidiary = 2   Versatile High-Performance Coatings,
--                                 LLC - the only real subsidiary.
--   c.stage LIKE 'CUSTOMER%'      Customer per METHODOLOGY.md: stage starts
--   AND c.firstorderdate IS NOT NULL  with Customer AND has a first order.
--                                 Everything else in the table is a Lead.
--   c.category NOT IN (2, 14)     2 = Garage Experts (franchisees), 14 = Vendor.
--                                 Verified with BUILTIN.DF(c.category) on
--                                 2026-09-05: 3 = DIY (406 customers all-time),
--                                 4 = Contractor (2,620), 7 = CA Will Call (17,
--                                 retained - not excluded by the canonical
--                                 join). NOT IN also drops NULL-category rows.
--   c.datecreated in [cohort_from, cohort_to)   COHORT = record-creation month.
--                                 Never first-order month.
--   t.type IN (4 types)           CustInvc, CashSale, CustCred, CustRfnd -
--                                 drop the last two and returns vanish.
--   tl.mainline = 'F'             excludes the transaction summary line.
--   tl.taxline  = 'F'             excludes tax lines.
--   tl.item IS NOT NULL           necessary but not sufficient on its own.
--   i.itemtype IS NOT NULL        CRITICAL: freight/shipping items (FedEx, UPS,
--                                 USPS, SEFL, warehouse) post with NULL
--                                 itemtype and would inflate "NET" silently.
--   SUM(-tl.foreignamount)        sign flip is required; COALESCE so an empty
--                                 period yields 0 not NULL.
--   M1 window                     t.trandate in the SAME calendar month as
--                                 c.datecreated (month-bounded date range,
--                                 equivalent to TO_CHAR(t.trandate,'YYYY-MM')
--                                 = TO_CHAR(c.datecreated,'YYYY-MM') but
--                                 sargable).
--   First-90-days window          t.trandate >= TRUNC(c.datecreated) AND
--                                 t.trandate < TRUNC(c.datecreated) + 90,
--                                 identical to the canonical cohorts_m13.sql.
--                                 WHY TRUNC: customer.datecreated is a
--                                 timestamp; the methodology-table spelling
--                                 `trandate >= datecreated` compares a
--                                 midnight trandate against e.g. 14:32 on the
--                                 creation day and EXCLUDES same-day first
--                                 orders. Confirmed here 2026-09-05: the
--                                 timestamp form gave 123,692.97 for cohort
--                                 2025-09 (= cohorts_m13's recorded
--                                 "v1_timestamp_basis"), TRUNC gives
--                                 160,501.03 (= cohorts_m13.m13_net_revenue).
--                                 The extra absolute trandate bounds (cohort
--                                 start .. cohort end + 90 days) are redundant
--                                 with the per-customer window and exist only
--                                 to let the planner prune transactions.
--                                 Reported only for cohorts whose window is
--                                 closed: periods.m13_closed(cohort, as_of),
--                                 i.e. month_end + 90 days <= as_of.
--
-- NOTE on M1 vs first-90-days: M1 is month-bounded and therefore includes
-- invoices dated earlier in the creation month than the customer record itself
-- (back-dated invoices), whereas the 90-day window starts on the creation day.
-- So for a given state/cohort M1 can still slightly exceed first-90-days.

-- 1. New customers by default ship-to state, whole window in one call.
SELECT
      ea.state                                      AS state,
      COUNT(c.id)                                   AS customers
FROM      customer c
LEFT JOIN customeraddressbook              cab ON cab.entity = c.id
                                               AND cab.defaultshipping = 'T'
LEFT JOIN customeraddressbookentityaddress ea  ON ea.nkey = cab.addressbookaddress
WHERE     c.subsidiary = 2
      AND c.stage LIKE 'CUSTOMER%'
      AND c.firstorderdate IS NOT NULL
      AND c.category NOT IN (2, 14)
      AND c.datecreated >= TO_DATE(:window_from, 'YYYY-MM-DD')
      AND c.datecreated <  TO_DATE(:window_to,   'YYYY-MM-DD')
GROUP BY  ea.state
ORDER BY  2 DESC;

-- 2. M1 NET revenue by state, ONE cohort month per call.
SELECT
      ea.state                                      AS state,
      COALESCE(SUM(-tl.foreignamount), 0)           AS m1_net_revenue,
      COUNT(DISTINCT t.id)                          AS transactions
FROM      transaction     t
JOIN      transactionline tl ON tl.transaction = t.id
JOIN      item            i  ON i.id = tl.item
JOIN      customer        c  ON c.id = t.entity
LEFT JOIN customeraddressbook              cab ON cab.entity = c.id
                                               AND cab.defaultshipping = 'T'
LEFT JOIN customeraddressbookentityaddress ea  ON ea.nkey = cab.addressbookaddress
WHERE     t.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
      AND t.subsidiary = 2
      AND tl.mainline  = 'F'
      AND tl.taxline   = 'F'
      AND tl.item      IS NOT NULL
      AND i.itemtype   IS NOT NULL
      AND c.category   NOT IN (2, 14)
      AND c.stage LIKE 'CUSTOMER%'
      AND c.firstorderdate IS NOT NULL
      AND c.datecreated >= TO_DATE(:cohort_from, 'YYYY-MM-DD')
      AND c.datecreated <  TO_DATE(:cohort_to,   'YYYY-MM-DD')
      AND t.trandate    >= TO_DATE(:cohort_from, 'YYYY-MM-DD')
      AND t.trandate    <  TO_DATE(:cohort_to,   'YYYY-MM-DD')
GROUP BY  ea.state;

-- 3. First-90-days NET revenue by state, ONE cohort month per call, closed
--    cohorts only (:m13_to = cohort_to + 89 days, i.e. month_end + 90).
SELECT
      ea.state                                      AS state,
      COALESCE(SUM(-tl.foreignamount), 0)           AS m13_net_revenue,
      COUNT(DISTINCT t.id)                          AS transactions
FROM      transaction     t
JOIN      transactionline tl ON tl.transaction = t.id
JOIN      item            i  ON i.id = tl.item
JOIN      customer        c  ON c.id = t.entity
LEFT JOIN customeraddressbook              cab ON cab.entity = c.id
                                               AND cab.defaultshipping = 'T'
LEFT JOIN customeraddressbookentityaddress ea  ON ea.nkey = cab.addressbookaddress
WHERE     t.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
      AND t.subsidiary = 2
      AND tl.mainline  = 'F'
      AND tl.taxline   = 'F'
      AND tl.item      IS NOT NULL
      AND i.itemtype   IS NOT NULL
      AND c.category   NOT IN (2, 14)
      AND c.stage LIKE 'CUSTOMER%'
      AND c.firstorderdate IS NOT NULL
      AND c.datecreated >= TO_DATE(:cohort_from, 'YYYY-MM-DD')
      AND c.datecreated <  TO_DATE(:cohort_to,   'YYYY-MM-DD')
      AND t.trandate    >= TRUNC(c.datecreated)
      AND t.trandate    <  TRUNC(c.datecreated) + 90
      AND t.trandate    >= TO_DATE(:cohort_from, 'YYYY-MM-DD')
      AND t.trandate    <  TO_DATE(:m13_to,      'YYYY-MM-DD')
GROUP BY  ea.state;
