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
2. Copy `.env.example` to `.env` and set `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD`. Ask IT for a **read-only** login.
3. Copy `engine/sql/billing.example.sql` to `engine/sql/billing.sql` and use your own table names. Keep the **output column names**, because the app reads them. The single `?` in the query is the first billing date to pull.
4. Restart the app, open *Data* and press *Test connection*.

The computer running the app must be able to reach the SQL Server (company network or VPN).

## How the dataset is built

*Data > Build dataset* does this:

1. Keeps the billing months you ticked as the baseline (default: the last three complete months). **Units** are the average monthly packs over them.
2. Keeps only customer x SKU lines that exist in the offer file. That is the scope of the tool.
3. Takes MRP, trade margin, offer and GST from the offer file, weighted by packs.
4. Works out the promo rate from the offer, and a "rebates and other" rate from the gap between list invoice and billed price, so net revenue ties to billing.
5. Spreads the MIS costs to each row using the rules in `config/attribution.csv` (see `HOW_IT_WORKS.md`).
6. Writes `data/rows.csv` and `data/meta.json`.

The simulator and P&L switch to the new data when the build finishes.

## Checking a build

After every build, read the quality report and **the reconciliation** on the Data page before trusting the P&L:

- **Reconciliation:** each cost line as a share of net revenue in the model, next to the same share in the MIS itself, for each customer group. Some gap is expected, because the MIS covers more customers and months than the offer file does. A large gap on one line shows where the attribution needs attention.
- **Quality:** share of billing revenue with an unreadable pack size, how much of billing falls inside the offer-file scope, revenue whose price gap was capped as a probable data problem, and rows dropped.
- Compare the **Total** row on the P&L page, with no levers set, against a P&L finance already trusts.

## Team inputs in `config/` (safe to share)

| File | Contents |
|---|---|
| `assumptions.csv` | Elasticity and milk share per product family. Edit and restart. A build never overwrites it |
| `attribution.csv` | For each MIS cost line, `volume` (per pack) or `revenue` (% of net revenue). Change it, then rebuild |

Global constants for the volume response are at the top of `engine/simulate.py`. Treat the volume response as indicative until the elasticities are calibrated from past promo and price-change history.

## Refreshing

When new billing months arrive: *Refresh from SQL*, tick the months you want as the baseline, and rebuild. When the offer file changes: upload it again and rebuild.
