-- Revenue to date for ONE acquisition cohort: every NET dollar the cohort's
-- customers have produced from the day their record was created up to (but
-- not including) :through.
--
-- This is the LIVE component of the cohort file. METHODOLOGY.md: "revenue-
-- to-date on the frozen basis = frozen M1 + live repeat". M1 is frozen once
-- published; the repeat part grows every month and is pulled fresh on every
-- ingest. The adapter forms repeat_revenue_live = revenue_to_date - m1 and
-- refuses a result where revenue to date is below M1 (impossible unless
-- credits were applied outside the M1 window - investigate, do not publish).
--
-- Window starts at TRUNC(c.datecreated), the creation DAY, for the reason
-- given in cohorts_m13.sql: datecreated is a timestamp and a same-day sale
-- compares midnight against 14:32 and drops out under the bare form.
--
-- :through is exclusive. Pass the day AFTER the as-of date so the as-of
-- day's transactions are included; the build records the as-of date in the
-- page footer, and the two must agree.
--
-- Same canonical join as cohorts_m1.sql / net_revenue_monthly.sql; the
-- clause reasons live there. One cohort month per call (180 s timeout).
-- Always returns exactly one row.
SELECT
      COUNT(DISTINCT c.id)                          AS customers,
      COALESCE(SUM(-tl.foreignamount), 0)           AS revenue_to_date,
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
      AND t.trandate       < TO_DATE(:through, 'YYYY-MM-DD')
