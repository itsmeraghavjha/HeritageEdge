-- Example billing query for HeritageEdge.
--
-- To use it: copy this file to billing.sql (same folder), replace the table and column names with your own,
-- and keep the OUTPUT column names exactly as they are. billing.sql is ignored by git, so your real table
-- names stay out of the repository. To share it with the team, send the file directly or commit it once the
-- repository is private (remove the billing.sql line from .gitignore).
--
-- The app reads these output columns:
--   BillMonth, CustomerGroup, CustomerID, SalesOfficeID, SalesOfficeName, CITY, STATE, MaterialCode,
--   ProductFamily, Variant, PackSize, SalesUnit, Qty_LtrKg, Packs, InvoiceValue_exTax, Tax,
--   InvoiceValue_inclTax, BillingLines
--
-- The single ? is the first billing date to pull (the "Pull billing from" box on the Data page). It is
-- optional: a query with no ? uses the date written in the query and the box is ignored.

;WITH base AS (
  SELECT
    DATEFROMPARTS(YEAR(s.BillingDate), MONTH(s.BillingDate), 1) AS BillMonth,
    s.CustomerGroup,
    s.CustomerID,
    s.SalesOfficeID,
    c.OfficeName  AS SalesOfficeName,
    c.CITY,
    c.STATE,
    s.MaterialCode,
    s.ProductLevel1 AS ProductFamily,
    s.ProductLevel3 AS Variant,
    s.ProductLevel5 AS PackSize,
    s.SalesUnit,
    s.SalesQuantity,
    s.TotalAmount,
    s.TotalTax,
    s.NetAmount,
    -- pack size in litres / kg, read from text such as '500 ML' or '1 KG'
    CASE
      WHEN UPPER(LTRIM(RTRIM(s.ProductLevel5))) LIKE '[0-9]% ML'  THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductLevel5))),' ML','')  AS float) / 1000.0
      WHEN UPPER(LTRIM(RTRIM(s.ProductLevel5))) LIKE '[0-9]% GMS' THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductLevel5))),' GMS','') AS float) / 1000.0
      WHEN UPPER(LTRIM(RTRIM(s.ProductLevel5))) LIKE '[0-9]% KG'  THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductLevel5))),' KG','')  AS float)
      WHEN UPPER(LTRIM(RTRIM(s.ProductLevel5))) LIKE '[0-9]% L'   THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductLevel5))),' L','')   AS float)
    END AS PackBase
  FROM [YourDatabase].[dbo].[BillingFact] s                       -- CHANGE: your billing fact table
  JOIN [YourDatabase].[dbo].[SalesOfficeMaster] c                 -- CHANGE: your sales office master
    ON s.SalesOfficeID = c.PlantCode
  WHERE s.BillingDate >= ?
    AND s.CustomerGroup IN ('Institutions','E-Commerce','Modern Formats')
)
SELECT
  BillMonth, CustomerGroup, CustomerID,
  SalesOfficeID, SalesOfficeName, CITY, STATE,
  MaterialCode, ProductFamily, Variant, PackSize, SalesUnit,
  SUM(SalesQuantity)                          AS Qty_LtrKg,
  SUM(CASE WHEN PackBase > 0 THEN SalesQuantity / PackBase END) AS Packs,
  SUM(TotalAmount)                            AS InvoiceValue_exTax,
  SUM(TotalTax)                               AS Tax,
  SUM(NetAmount)                              AS InvoiceValue_inclTax,
  COUNT(*)                                    AS BillingLines
FROM base
GROUP BY BillMonth, CustomerGroup, CustomerID,
         SalesOfficeID, SalesOfficeName, CITY, STATE,
         MaterialCode, ProductFamily, Variant, PackSize, SalesUnit
ORDER BY BillMonth, CustomerGroup, CustomerID, SalesOfficeID, MaterialCode;
