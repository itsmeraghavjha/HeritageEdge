# Data guide: moving from sample data to real data

All the numbers in the app today come from `engine/data.py`. They are made up but realistic. Here is what to replace.

## 1. Baseline volumes (the main thing)

`build_rows()` returns one row per chain x sales office x SKU:

```python
{"chain": "dmart", "state": "TS", "so": "hyd-c", "sku": "toned-500", "units": 114000}
```

`units` is the **baseline monthly volume** for that combination. Replace `build_rows()` with a function that reads your actuals (a CSV, Excel file or database query) and returns the same shape. The `chain`, `state`, `so` and `sku` values must match the ids in the tables below.

## 2. Reference tables in `engine/data.py`

| Table | Fields to check |
|---|---|
| `CHAINS` | `trade_margin` (share of MRP), `rebate` and `base_promo` (shares of invoice value), `states` the chain operates in. `size` is only used to generate sample volumes, so it can be removed with the sample generator |
| `SKUS` | `mrp`, `cogs` (per unit), `milk_share` (share of COGS driven by milk), `freight` (per unit), `elasticity` (negative number) |
| `STATES` | `freight_factor` (1.0 = the cheapest state to serve) |
| `SALES_OFFICES` | Which state each belongs to. `weight` is only used for sample volumes |

## 3. Global assumptions in `engine/simulate.py`

| Constant | Now | Meaning |
|---|---|---|
| `SD_PCT` | 3.5% | Sales and distribution cost as a share of net revenue |
| `PROMO_RESPONSE_BOOST` | 3.0 | How much harder shoppers react to a promo than to a price change |
| `PUSH_PER_PP_TRADE` | 0.008 | Volume lift per +1 pp of trade margin |
| `PUSH_PER_PP_REBATE` | 0.004 | Volume lift per +1 pp of rebates and display fees |

## 4. Calibrating the response assumptions

The elasticities and the three constants above decide whether a promo "pays back" in the simulator, so they matter most. Good sources:

- Past promo periods: compare volume and margin during the promo with a baseline of similar weeks.
- Past price changes by SKU, for elasticity.
- Chain-level history of display and margin changes.

Until this is done, treat the volume response as indicative. The rate effects (price, promo cost, trade terms, costs) are exact arithmetic on the inputs.

## 5. Checking the result

After loading real data, run `pytest`. The tests check that the P&L adds up, the drill-down rows sum to their parents and the bridge reconciles. Then compare the **Total** row on the P&L page, with no levers set, against a P&L you already trust.
