-- The latest billing day in the data. The Data page uses it to count the days of billing so far in a month that is
-- still running, because the database is loaded up to yesterday and the To box may say today.
-- Same two ? as billing.sql (first billing date, and the day after the last), and the same customer groups.
-- It holds table names only. If it fails, the pull still works and the page falls back to the To date.
SELECT MAX(s.BillingDate)
FROM [HeritageBI].[DW].[fSales] s
WHERE s.BillingDate >= ?
  AND s.BillingDate < ?
  AND s.CustomerGroup IN ('Institutions','E-Commerce','Modern Formats');
