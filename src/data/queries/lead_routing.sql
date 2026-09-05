-- Lead routing: customer-table records ASSIGNED to each sales rep per month,
-- and how many of those have since CONVERTED to Customers.
--
-- This is a customer-table-only query (no transaction join), so it is safe to
-- run across the whole 15-month range in one call: it returns ~4 rows per
-- month (one per rep + one NULL row for Unassigned), far under the 5000-row
-- cap, and finishes in well under the 180-second SuiteQL timeout.
--
-- Every clause is load-bearing:
--
--   TO_CHAR(c.datecreated,'YYYY-MM')   the month a record was CREATED, i.e.
--                                       when it landed in the rep's pipeline.
--                                       Never first-order month - a record
--                                       created in March and ordering in May
--                                       belongs to March.
--   c.salesrep                          the rep the record is routed to. NULL
--                                       = Unassigned. The id is grouped here
--                                       and resolved to a name with
--                                       BUILTIN.DF(e.id) against employee in a
--                                       second tiny query, because
--                                       BUILTIN.DF inside GROUP BY is rejected
--                                       in some contexts. Known ids:
--                                       8766 Alexis Garcia, 5803 Dan Newhard,
--                                       16226 Parker Strong, 15711 = "other".
--   c.subsidiary = 2                    Versatile High-Performance Coatings,
--                                       LLC. The only real subsidiary.
--   c.category NOT IN (2, 14)           2 = Garage Experts (franchisees),
--                                       14 = Vendor. Neither is a lead. The
--                                       remaining categories (verified via
--                                       BUILTIN.DF on 2026-09-05) are
--                                       3 = DIY, 4 = Contractor, plus a
--                                       handful of 7 = CA Will Call and
--                                       9 = Facility Manager. NOTE: NOT IN
--                                       also drops the 2 records with a NULL
--                                       category, matching the canonical
--                                       net_revenue_monthly.sql behaviour.
--   COUNT(c.id)                         records ASSIGNED in the month.
--   COUNT(CASE WHEN ... THEN c.id END)  records CONVERTED: a Customer per
--                                       METHODOLOGY.md is stage LIKE
--                                       'CUSTOMER%' AND firstorderdate IS NOT
--                                       NULL. Stage alone is insufficient -
--                                       4 LEAD-stage records carry a
--                                       firstorderdate and 3,388 CUSTOMER-stage
--                                       records have none. COUNT(field) is
--                                       used rather than SUM(CASE ...) per the
--                                       house rule.
--   TO_DATE(...) bounds                 half-open [from, to) on datecreated;
--                                       datecreated carries a time component
--                                       so the bounds and the TO_CHAR grouping
--                                       agree at month edges.
--
-- Converted is a to-date measure: a lead assigned in July that first orders in
-- September raises July's converted count on the next pull. Expect this
-- column to drift upward for recent months until frozen.
SELECT
      TO_CHAR(c.datecreated, 'YYYY-MM')                              AS month,
      c.salesrep                                                     AS rep_id,
      COUNT(c.id)                                                    AS assigned,
      COUNT(CASE WHEN c.stage LIKE 'CUSTOMER%'
                  AND c.firstorderdate IS NOT NULL THEN c.id END)    AS converted
FROM      customer c
WHERE     c.subsidiary  = :subsidiary_id
      AND c.category    NOT IN (:cat_garageexperts, :cat_vendor)
      AND c.datecreated >= TO_DATE(:date_from, 'YYYY-MM-DD')
      AND c.datecreated <  TO_DATE(:date_to,   'YYYY-MM-DD')
GROUP BY  TO_CHAR(c.datecreated, 'YYYY-MM'), c.salesrep
ORDER BY  1, 3 DESC

-- Companion lookup (rep_id -> display name):
-- SELECT e.id AS rep_id, BUILTIN.DF(e.id) AS rep_name
-- FROM employee e WHERE e.id IN (5803, 8766, 15711, 16226)
