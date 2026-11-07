# Data guide

The app runs on one dataset: a table with a row per chain x customer group x sales office x SKU. Without real data it uses `sample_data/rows.csv` (synthetic). With real data it uses `data/rows.csv`, which the app builds for you from three sources.

**Real data is confidential and stays on the computer running the app. It is ignored by git. Never commit it.**

## The three sources

| Source | What it gives | How it arrives |
|---|---|---|
| **SQL Server billing** | Monthly packs and invoice value by customer, sales office and SKU | Pulled by the app: *Data > Refresh from SQL* |
| **Offer file** (`.xlsb` or `.xlsx`) | List price (MRP), trade margin, offers, GST per customer x SKU, and which customer belongs to which chain (`Cluster`) | Uploaded on the Data page |
| **MIS workbook** (`.xlsx`, sheet `SAP_MIS_DATABASE`) | COGS, S&D, P&A, interest, over/under and marketing by SKU, sales office and customer group | Uploaded on the Data page |

## Setting up the SQL connection

1. Install `pyodbc` (`pip install pyodbc`) and the Microsoft ODBC Driver for SQL Server.
2. Copy `.env.example` to `.env` and set `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER` and `SQL_PASSWORD`. Set `SQL_PORT` too if the server is not on the default port (or write it as `host,1433` in `SQL_SERVER`). Ask IT for a **read-only** login. `.env` is never committed.
3. The billing query is `engine/sql/billing.sql`, shared in the repo. It holds table names only, no login. If you change it, keep the **output column names** listed in its header, because the app reads them (a test checks this). The single `?` is the first billing date to pull, and comments in the file are stripped before it runs.
4. Restart the app, open *Data* and press *Test connection*.

The computer running the app must be able to reach the SQL Server (company network or VPN).

## How the dataset is built

*Data > Build dataset* does this:

1. Keeps the billing months you ticked as the baseline (default: the last three complete months). **Units** are the average monthly packs over them.
2. Keeps only customer x SKU lines that exist in the offer file. That is the scope of the tool.
3. Takes MRP, trade margin, offer and GST from the offer file, weighted by packs.
4. Works out the promo rate from the offer, and an "other deductions" rate from the gap between list invoice and billed price, so net revenue ties to billing.
5. Spreads the MIS costs to each row using the rules in `config/attribution.csv` (see `HOW_IT_WORKS.md`).
6. Writes `data/rows.csv` and `data/meta.json`.

The simulator and P&L switch to the new data when the build finishes.

## Checking a build

### The MIS is cut to the base months

Billing and the MIS must describe the same period. The build keeps only the MIS rows for the base months you tick, and stops with a message if the MIS has no rows for one of them (it lists the months it does have). Without this, per-pack costs were averaged over every month in the MIS sheet while revenue came from the chosen billing months, which overstated margin when prices or costs moved between months. The Data page shows "MIS months used for costs" after a build.

The month column is found by its header (Month, Period and similar) and an optional Year column (Year, FY and similar). Months can be dates, `202607`, `2026-07`, `Jul-26`, `July 2026`, or just `Jul`. A year is read as a calendar year (`2026`) or a financial year from April (`2026-27`). If the MIS gives no year, months are matched by name only and the page says so. If the headers are different, set `MIS_MONTH_COLUMN` and `MIS_YEAR_COLUMN` in `.env` to the exact header text; the error message lists the columns found. A single base month is allowed and avoids blending prices from different months, but one month can be unrepresentative (festivals, stock pushes), so choose what suits the question.

After every build, read the quality report, **the reconciliation** and **the revenue per pack check** on the Data page before trusting the P&L.

The revenue per pack check exists because COGS, S&D and over/under are taken from the MIS *per pack*, while revenue per pack comes from billing. If billing sells the same pack for more than the MIS shows, those costs look smaller as a share of revenue and the margin is overstated. The check compares the two for matching SKU, sales office and customer group, by group and family, lists the SKUs that differ most, and shows whether the gap is spread evenly (a definition difference, such as what the MIS deducts from revenue or how it counts packs) or sits on a few lines (a data problem on those SKUs or offices). It is written to `meta.json` as `price_check`.

Two further checks sit under it. **Is it the customers?** The MIS price for a SKU, sales office and group averages every customer in that cell, while the model only holds customers in the offer file. The check compares the MIS with all billing in the same cells and splits it into offer-file customers and the rest, so a gap caused by who is in the cell shows up separately from a gap in how revenue is defined. **Packs billed against packs in the MIS** (same cells) shows whether both count packs the same way; a ratio far from 100% means a different unit. Costs are compared SKU by SKU, so choosing litres instead of packs would not change the result unless the counts themselves differ.

The reconciliation:

- **Reconciliation:** each cost line as a share of net revenue in the model, next to the same share in the MIS itself, for each customer group. Some gap is expected, because the MIS covers more customers and months than the offer file does. A large gap on one line shows where the attribution needs attention.
- **Quality:** share of billing revenue with an unreadable pack size, how much of billing falls inside the offer-file scope, revenue whose price gap was capped as a probable data problem, and rows dropped.
- Compare the **Total** row on the P&L page, with no levers set, against a P&L finance already trusts.

## Team inputs in `config/` (safe to share)

| File | Contents |
|---|---|
| `assumptions.csv` | Elasticity per product family. Edit and restart. A build never overwrites it |
| `attribution.csv` | For each MIS cost line, `volume` (per pack) or `revenue` (% of net revenue). Change it, then rebuild |

Global constants for the volume suggestion are at the top of `engine/simulate.py`. They only feed the hint shown next to the volume input, which users set themselves. Treat the suggestion as indicative until the elasticities are calibrated from past promo and price-change history.

## Refreshing

When new billing months arrive: *Refresh from SQL*, tick the months you want as the baseline, and rebuild. When the offer file changes: upload it again and rebuild.

## Billed price against the offer file

The Data page also compares, for each chain and SKU, the price the offer file implies, `(MRP x (1 - trade) - offer) / (1 + GST)`, with the price billing charged. It shows the share of revenue billed at the offer-file price, below it and above it, and the ten chain and SKU pairs with the largest rupee difference. Net revenue follows billing, so the gap does not move margin. It moves money between promo and "other deductions". Typical causes are an offer that changed during the month, several offer rows for one customer and SKU (the build averages them), and a scheme that the offer file does not hold.

## Where other deductions come from

"Other deductions" is the plug: net revenue follows billing, so whatever the offer file does not explain lands there. After every build the Data page splits that gap (`engine/gapcheck.py`, saved as `gap_breakdown` in `meta.json`) into causes, in this order, so the pieces add up to the whole gap:

1. **GST rate.** Billing's `Tax` column gives the rate it really charged; if it differs from the offer file's GST%, the offer price was converted to ex-GST with the wrong rate. Skipped (and said so) when billing has no tax.
2. **Several offer rows** for one customer and SKU, which the build averages. The piece is what would disappear if the row best matching billing had been the one in force: an upper bound.
3. **A price far from the offer file** (over 40% either way): usually a unit, pack-size or customer-mapping problem.
4. **A gap that changes between base months**: terms moved during the period, and the offer file holds one set.
5. **A steady gap**: the same every month. A scheme, credit note or free goods the offer file does not hold.

The page also lists the ten chain and SKU pairs with the largest gap left, each with its likely cause. Shares are of billed revenue; "net" adds to the total gap and "absolute" ignores sign. This check only explains the gap; it never changes the numbers. Fixes follow the cause: causes 1 and 3 are data fixes, 2 and 4 need a better offer file (one row per customer and SKU, with dates), and 5 is a real deduction to add to the offer file.

## Data checks (Data page, after a build)

`engine/integrity.py` runs 24 checks on the data already loaded and stores them in `meta.json` under `integrity`. Each has a status (ok, look at this, problem, not checked), the question it answers, evidence rows and what to do. A check that cannot run is "not checked"; no check can stop a build. Real data will show whichever the data earns; synthetic data trips some on purpose.

- Units: unit of sale is litres or kg only; packs are whole numbers; pack text agrees with the offer-file product name; the same SKU is priced alike across chains (a pack counted in the wrong unit shows as a constant gap).
- Coverage: where every rupee of billing went (waterfall: unreadable pack size, not in offer file, outside MIS groups, dropped at the end); returns and zero-quantity lines; repeated lines; each base month full; one chain and one group per customer; no covered customer missing.
- MIS match: same SKU x office x group cells on both sides; costs come from the cell, not a fallback; packs billed against packs in the MIS per SKU (same revenue with different packs = unit slip, both moving = volume difference); same months (steady ratio = definition, jump = partial month).
- Tax and offer file: billing tax is a real GST rate; GST and trade margin in the right units (5 not 0.05); one set of terms per customer x SKU; one MRP per SKU within a state (states may differ); no offer line dropped for missing terms.

Each check has a test in `tests/test_integrity.py`. Add a check by writing a `_function(c)` decorated with `check(...)` and adding it to `CHECKS`.

### The offer for the base months

The offer file's numeric offer columns ("On Invoice offer", "Off Invoice Offer in Value") describe the latest month only. Each month also has a text column ("Jul'26 - Month Offer"): "NLC Rs. N On Invoice Promo" means the chain pays N against list price MRP x (1 - trade margin), so the on-invoice offer is the difference; "Rs. N Promo - Off Invoice claim" is a claim paid later; blank is no offer. `prepare.monthly_offers` reads the column of each base month and averages them. If a base month has no column it falls back to the numeric columns and the check "The offer is read from the base months" warns. Checks added: a price gap with no offer involved (the file's own "Basic cost" equals MRP less trade, ex-GST, so a gap on no-offer lines is not an offer problem), the gap in each base month (shows an MRP that changed inside the period), offer months, off-invoice offers against the billed price, new or old MRP (the file also holds "OLD MRP"; the build uses "New MRP" for every month, and the check shows which one billing follows).
