-- The canonical NET revenue join. Deviate from this and numbers will be wrong.
--
-- NET means product only: excluding shipping and tax, with returns and credit
-- memos deducted. There is no gross figure anywhere in the output.
--
-- Every clause below is load-bearing:
--
--   t.type IN (4)          all four types, or returns are silently ignored.
--                          CustCred and CustRfnd sign-flip correctly under
--                          -tl.foreignamount.
--   tl.mainline = 'F'      excludes the transaction summary line.
--   tl.taxline  = 'F'      excludes tax lines.
--   tl.item IS NOT NULL    necessary but NOT sufficient on its own.
--   i.itemtype IS NOT NULL CRITICAL. Freight and shipping items (FedEx, UPS,
--                          USPS, SEFL, warehouse) post as items with a NULL
--                          itemtype. Without this clause they inflate "NET"
--                          revenue silently - it added $11,703 of shipping to
--                          Jan-Jul 2026 in v1 before it was caught.
--   c.category NOT IN      excludes GarageExperts franchisees and Vendor.
--                          IDs are resolved by name at ingest and asserted,
--                          because the two source documents disagree about
--                          which ID is which.
--   SUM(-tl.foreignamount) the sign flip is required.
--   COALESCE               mandatory, or SUM() returns NULL rather than 0 for
--                          an empty period.
--
-- The itemtype exclusion is measured, not assumed: ingest runs this query
-- with and without the clause and asserts the difference equals the freight
-- total. That distinguishes "we correctly excluded shipping" from "we lost
-- product revenue to a join failure", which are indistinguishable otherwise.
--
-- Chunk by month. A 14-month range hits the 180-second SuiteQL timeout.
SELECT
      TO_CHAR(t.trandate, 'YYYY-MM')                AS month,
      COALESCE(SUM(-tl.foreignamount), 0)           AS net_revenue,
      COUNT(DISTINCT t.id)                          AS transactions
FROM      transaction     t
JOIN      transactionline tl ON tl.transaction = t.id
JOIN      item            i  ON i.id = tl.item
JOIN      customer        c  ON c.id = t.entity
WHERE     t.type IN ('CustInvc', 'CashSale', 'CustCred', 'CustRfnd')
      AND t.subsidiary = :subsidiary_id
      AND tl.mainline  = 'F'
      AND tl.taxline   = 'F'
      AND tl.item      IS NOT NULL
      AND i.itemtype   IS NOT NULL
      AND c.category   NOT IN (:cat_garageexperts, :cat_vendor)
      AND t.trandate  >= TO_DATE(:date_from, 'YYYY-MM-DD')
      AND t.trandate   < TO_DATE(:date_to,   'YYYY-MM-DD')
GROUP BY  TO_CHAR(t.trandate, 'YYYY-MM')
ORDER BY  1
