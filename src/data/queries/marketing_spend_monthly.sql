-- Marketing spend by month and GL account, VHPC LLC.
--
-- Include GL 66212.* and 66215.*; EXCLUDE 96212.* (the NAF - the
-- GarageExperts franchisee fund, which is not our spend).
--
-- The NAF accounts mirror the marketing chart of accounts almost line for
-- line (96212.0016 is NAF Google, 66212.0016 is ours). Never widen these
-- patterns to a substring match: LIKE '%6212%' matches BOTH and would fold
-- the franchisee fund into VHPC marketing spend without any visible error.
--
-- posting = 'T' excludes non-posting transactions.
-- Chunk by year, never by multi-year range: long ranges hit the 180-second
-- SuiteQL timeout.
SELECT
      TO_CHAR(t.trandate, 'YYYY-MM')            AS month,
      a.acctnumber                              AS account,
      BUILTIN.DF(a.id)                          AS account_name,
      COALESCE(SUM(tal.amount), 0)              AS amount
FROM      transaction               t
JOIN      transactionline           tl  ON tl.transaction = t.id
JOIN      transactionaccountingline tal ON tal.transaction = t.id
                                       AND tal.transactionline = tl.id
JOIN      account                   a   ON a.id = tal.account
WHERE     t.posting     = 'T'
      AND t.subsidiary  = :subsidiary_id          -- 2 = Versatile High-Performance Coatings, LLC
      AND t.trandate   >= TO_DATE(:date_from, 'YYYY-MM-DD')
      AND t.trandate    < TO_DATE(:date_to,   'YYYY-MM-DD')
      AND (a.acctnumber LIKE '66212%' OR a.acctnumber LIKE '66215%')
      AND a.acctnumber NOT LIKE '96212%'
GROUP BY  TO_CHAR(t.trandate, 'YYYY-MM'), a.acctnumber, BUILTIN.DF(a.id)
ORDER BY  1, 2
