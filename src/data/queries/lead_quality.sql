-- Lead quality: contact-data capture and conversion for customer-table
-- records CREATED in a month, blended and for rep-ASSIGNED records only.
--
-- This is a customer-table-only query (no transaction join), so the whole
-- 15-month range runs in one call well under the 180-second SuiteQL timeout
-- and returns one row per month, far under the 5000-row cap. Ingest still
-- writes one snapshot per month (data/snapshots/<YYYY-MM>/lead_quality.json).
--
-- Every clause is load-bearing:
--
--   TO_CHAR(c.datecreated,'YYYY-MM')   the month the record was CREATED
--                                       (cohort). Never first-order month.
--   c.subsidiary = 2                    Versatile High-Performance Coatings,
--                                       LLC - the only real subsidiary.
--   c.category NOT IN (2, 14)           2 = Garage Experts (franchisees),
--                                       14 = Vendor. Neither is a lead.
--                                       Verified with BUILTIN.DF(c.category)
--                                       on 2026-09-05: 3 = DIY, 4 = Contractor
--                                       (the two real customer categories),
--                                       plus 7 = CA Will Call (17 records) and
--                                       9 = Facility Manager (1 record). NOT IN
--                                       also drops the 2 NULL-category records,
--                                       matching net_revenue_monthly.sql.
--   COUNT(c.id)                         total_records created in the month.
--   COUNT(TRIM(c.phone))                with_phone. In SuiteQL (Oracle) the
--                                       empty string IS NULL, so the textbook
--                                       `phone IS NOT NULL AND TRIM(phone) <> ''`
--                                       can never be true and returns 0 for
--                                       every month (confirmed 2026-09-05).
--                                       COUNT(TRIM(x)) counts exactly the rows
--                                       where x is non-NULL and not all
--                                       whitespace, which is the intended test.
--                                       c.mobilephone and c.altphone were
--                                       checked and are NULL for every record
--                                       in 2025-06..2026-08, so c.phone alone is
--                                       the phone-capture field; the two extra
--                                       columns are kept as a standing guard.
--   COUNT(TRIM(c.email))                with_email, same NULL-safe test.
--   customers                           a Customer per METHODOLOGY.md is
--                                       stage LIKE 'CUSTOMER%' AND
--                                       firstorderdate IS NOT NULL. Stage alone
--                                       is insufficient. Measured to date at
--                                       pull time, so this drifts UP for recent
--                                       months as leads convert.
--   COUNT(c.salesrep)                   assigned_records: routed to a rep
--                                       (salesrep NOT NULL). NULL = unassigned.
--   assigned_with_phone / _email        COUNT(CASE WHEN salesrep IS NOT NULL
--                                       THEN TRIM(field) END) - the same
--                                       capture test restricted to assigned
--                                       records, so the "what reps actually
--                                       receive" rate can sit beside the
--                                       blended rate.
--   COUNT(...) not SUM(CASE ...)        house rule.
--   TO_DATE(...) bounds                 half-open [from, to) on datecreated,
--                                       which carries a time component, so the
--                                       bounds and the TO_CHAR grouping agree
--                                       at month edges.
--
-- Derived in Python, never in SQL: phone_capture_pct = with_phone /
-- total_records * 100; email_capture_pct likewise; conversion_pct =
-- customers / total_records * 100; assigned_phone_capture_pct =
-- assigned_with_phone / assigned_records * 100. Every rate carries its
-- denominator in the snapshot body.
SELECT
      TO_CHAR(c.datecreated, 'YYYY-MM')                                 AS month,
      COUNT(c.id)                                                       AS total_records,
      COUNT(TRIM(c.phone))                                              AS with_phone,
      COUNT(TRIM(c.mobilephone))                                        AS with_mobile,
      COUNT(TRIM(c.altphone))                                           AS with_alt,
      COUNT(COALESCE(TRIM(c.phone), TRIM(c.mobilephone), TRIM(c.altphone)))
                                                                        AS with_any_phone,
      COUNT(TRIM(c.email))                                              AS with_email,
      COUNT(CASE WHEN c.stage LIKE 'CUSTOMER%'
                  AND c.firstorderdate IS NOT NULL THEN c.id END)       AS customers,
      COUNT(c.salesrep)                                                 AS assigned_records,
      COUNT(CASE WHEN c.salesrep IS NOT NULL THEN TRIM(c.phone) END)    AS assigned_with_phone,
      COUNT(CASE WHEN c.salesrep IS NOT NULL THEN TRIM(c.email) END)    AS assigned_with_email
FROM      customer c
WHERE     c.subsidiary  = :subsidiary_id
      AND c.category    NOT IN (:cat_garageexperts, :cat_vendor)
      AND c.datecreated >= TO_DATE(:date_from, 'YYYY-MM-DD')
      AND c.datecreated <  TO_DATE(:date_to,   'YYYY-MM-DD')
GROUP BY  TO_CHAR(c.datecreated, 'YYYY-MM')
ORDER BY  1
