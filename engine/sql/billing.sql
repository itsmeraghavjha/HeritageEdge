-- Monthly billing by customer, sales office and SKU, from HeritageBI.
-- Shared with the team. It holds table names only: the login (host, port, user, password) is in each person's .env.
--
-- The single ? is the first billing date to pull (the "Pull billing from" box on the Data page). It is optional:
-- a query with no ? uses the date written in it and the box is ignored.
--
-- The app reads these output columns, so keep their names if you change the query:
--   BillMonth, CustomerGroup, CustomerID, SalesOfficeID, SalesOfficeName, CITY, STATE, MaterialCode,
--   ProductFamily, Variant, PackSize, SalesUnit, Qty_LtrKg, Packs, InvoiceValue_exTax, Tax,
--   InvoiceValue_inclTax, BillingLines
;WITH base AS (
  SELECT
    DATEFROMPARTS(YEAR(s.BillingDate), MONTH(s.BillingDate), 1) AS BillMonth,
    s.CustomerGroup,
    s.CustomerID,
    s.SalesOfficeID,
    c.PLANT_NAME  AS SalesOfficeName,
    c.CITY,
    c.STATE,
    s.MaterialCode,
    s.ProductHeirachy1 AS ProductFamily,
    s.ProductHeirachy3 AS Variant,
    s.ProductHeirachy5 AS PackSize,
    s.SalesUnit,
    s.SalesQuantity,
    s.TotalAmount,
    s.TotalTax,
    s.NetAmount,
    -- pack size in litres / kg
    CASE
      WHEN UPPER(LTRIM(RTRIM(s.ProductHeirachy5))) LIKE '[0-9]% ML'  THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductHeirachy5))),' ML','')  AS float) / 1000.0
      WHEN UPPER(LTRIM(RTRIM(s.ProductHeirachy5))) LIKE '[0-9]% GMS' THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductHeirachy5))),' GMS','') AS float) / 1000.0
      WHEN UPPER(LTRIM(RTRIM(s.ProductHeirachy5))) LIKE '[0-9]% KG'  THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductHeirachy5))),' KG','')  AS float)
      WHEN UPPER(LTRIM(RTRIM(s.ProductHeirachy5))) LIKE '[0-9]% L'   THEN TRY_CAST(REPLACE(UPPER(LTRIM(RTRIM(s.ProductHeirachy5))),' L','')   AS float)
    END AS PackBase
  FROM [HeritageBI].[DW].[fSales] s
  JOIN [HeritageBI].[DW].[dsalesofficemaster] c
    ON s.SalesOfficeID = c.PLANT
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
