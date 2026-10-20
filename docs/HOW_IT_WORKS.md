# How HeritageEdge works

This explains what the numbers mean and how the levers move them. It is written for both the MRF team and developers.

## What the tool is for

The MRF team wants to try a promo, price change or change in chain terms **before** committing to it, and see the effect on net revenue, margin and contribution. The simulator does that. The P&L page shows the same numbers as a statement, cut by chain, customer group, state, city, sales office, product category, variant, pack size and SKU.

Everything is a **monthly run-rate**. "Baseline" is the position built from your data (the average of the months you chose) with no levers set. "Scenario" is the baseline with your levers applied.

## The P&L, line by line

Calculated for every row of data (one chain and customer group, in one sales office, for one SKU), then added up:

| Line | Meaning |
|---|---|
| MRP value | units x MRP (excluding GST) |
| minus Trade margin | the chain's share of MRP, from the offer file |
| **= Gross sales** | list invoice value |
| minus Promo | the current offer from the offer file, as a share of list invoice |
| minus Other deductions | the gap between list invoice and what was actually billed (credit notes, mix, other schemes) |
| **= Net revenue** | what was billed. Ties back to the billing data |
| minus COGS | attributed from the MIS. The COGS change lever scales the whole line |
| **= Gross margin** | |
| minus Over/under | attributed from the MIS |
| minus S&D / freight | attributed from the MIS |
| **= Contribution** | gross margin less over/under and S&D / freight. This is the MIS definition (its "MC") |
| minus P&A, Interest, Marketing | attributed from the MIS |
| **= Net margin** | the bottom line |

The MIS also has FOC, notional and OI lines. They were zero or close to zero in the months checked, so the model leaves them out. If they become material, add them as cost lines (`config/attribution.csv`, `engine/data.py`).

### Where each row's terms come from

- **Units:** average monthly packs billed over the base months you ticked on the Data page.
- **MRP, trade margin, offer, GST:** the offer file, averaged over the districts a customer buys in.
- **Net price:** billed invoice value divided by packs. Because "Other deductions" is the difference between list and billed price, net revenue always ties to billing.
- **Costs:** the MIS, spread to chains. See the next section.

## How MIS costs reach each chain

The MIS has no chain column. For every SKU x sales office x customer group it takes the MIS cost and spreads it to chains. `config/attribution.csv` sets the rule for each cost line:

- `volume`: every chain pays the same cost per pack in that cell.
- `revenue`: every chain pays the same percentage of its net revenue.

Where the MIS has no figure for an exact SKU x office cell, it falls back to the SKU average and then the product-family average.

**Limit of the method:** with volume-based lines, chains differ only through their mix of SKUs and offices, not through how costly they are to serve. It is an allocation, not a measured cost to serve. Splitting S&D by drop count, say, would need route-level data.

In a scenario, volume-based lines move with units and revenue-based lines move with net revenue.

## The levers

| Lever | What it changes | Applies to |
|---|---|---|
| MRP change (%) | Shelf price and invoice price together | Selected scope |
| Extra promo discount (pp) | Added to each row's current promo rate | Selected scope |
| Promo passed to shelf price (%) | How much of the promo reaches shoppers and so drives volume | Selected scope |
| Trade margin change (pp) | Chain margin as a share of MRP | Selected scope |
| Other deductions and display fees (pp) | Moves the "Other deductions" line. See the note below | Selected scope |
| Price sensitivity (x) | Scales every product family's elasticity. 1x uses `config/assumptions.csv` | Selected scope |
| Extra volume from visibility (%) | Volume you expect from display or activation, on top of price effects | Selected scope |
| COGS change (%) | The whole COGS line, from the MIS. Use it for a milk, packing or other input cost change | **Everything** |
| S&D / freight cost (%) | The S&D line | **Everything** |

"Selected scope" means the dimensions you limit under *Apply price, promo and chain terms to*. Any dimension can be limited, and nothing selected means everything. The two cost levers always apply to everything, because an input cost rise does not skip a chain.

**Note on "Other deductions and display fees":** in the real data this line also contains credit notes and mix effects, because it is the balancing item between list and billed price. Moving it by 1 pp changes that balancing line, which is not strictly "display fees". Treat it as "other deductions from invoice".

## How volume responds

Volume change for a row is the product of four effects:

1. **Price:** `(1 + price change) ^ elasticity`. Elasticity comes from `config/assumptions.csv` by product family, for example milk about -0.4 and cheese about -1.5.
2. **Promo:** the shelf-price drop from the promo, raised to `elasticity x PROMO_RESPONSE_BOOST` (currently 3). Shoppers react harder to a temporary promo than to a permanent price change.
3. **Chain terms:** more trade margin or extra deductions push extra volume (0.8% per +1 pp trade margin, 0.4% per +1 pp rebate).
4. **Visibility:** the "extra volume" lever, applied directly.

The constants are at the top of `engine/simulate.py`. **They are assumptions, not measurements.** See `DECISIONS.md`.

## Volume check: breakeven and response range

Volume response cannot be predicted reliably from our data: we have no competitor prices, seasonality or shelf data. So the simulator treats it as an assumption and shows two things that do not depend on trusting it.

**Breakeven.** At the new terms, how much extra volume pays for the change? With terms fixed, every line scales with packs, so the answer is exact: `required = (baseline contribution - contribution outside scope) / contribution in scope at new terms - 1`. It needs no elasticity. The card compares it with what the model assumes. Statuses: *nothing to pay back* (no lever changes what a pack earns), *already ahead* (a negative requirement: how much volume can be lost), *needs more volume*, and *more volume will not pay it back* (contribution in scope is zero or negative). Only lines in scope can sell more, even for cost levers.

**Low / base / high.** The same scenario with every volume change scaled to 0.5, 1.0 and 1.5 times (`RESPONSE_CASES` in `engine/simulate.py`). It shows whether the answer survives if shoppers react less or more than assumed. If contribution beats today in only some cases, the scenario depends on the assumption.

There is no built-in "realistic uplift" threshold; judge the breakeven against what the team has seen in past promos.

## The contribution bridge

The chart on the simulator page answers "which of my changes is doing what?". It applies lever groups one at a time (price, then promo, then trade terms, then costs) **with volume held at baseline**, so each bar is a pure rate effect. Whatever remains is the **volume response**. The bars always add up exactly to the scenario contribution.

## Where things live

```
app.py                  Flask routes, optional login, Data page API
engine/data.py          Loads the dataset, defines the dimensions
engine/sql_source.py    Pulls billing from SQL Server
engine/prepare.py       Builds the dataset from billing + offer file + MIS
engine/datastore.py     Uploads and background jobs for the Data page
engine/simulate.py      P&L maths, levers, bridge, grouping
engine/export.py        Excel export (grouped rows you can collapse, negatives in red, flat sheet for a PivotTable)
config/                 Elasticity and cost attribution rules
sample_data/            Synthetic dataset (in git)
data/                   Real data (ignored by git)
templates/, static/     Pages, scripts, styles
tests/                  Engine and build tests, synthetic data generator
```

API (all JSON): `/api/meta`, `/api/simulate`, `/api/pnl`, `/api/export`, and for the Data page `/api/data/status`, `/api/data/upload/<offer|mis>`, `/api/data/sql/test`, `/api/data/sql/pull`, `/api/data/build`. Simulate, P&L and export take `levers`, `scope`, and for P&L and export `dims` and `filters`.

## Known limits

- Scenarios you save are stored **in your own browser**, so they are not shared with the team yet.
- Login is a single shared password (`APP_PASSWORD`), not per-user.
- `python app.py` runs Flask's development server. Do not use it as a shared production deployment.
- Elasticities and the response constants are assumptions. The rate effects are exact arithmetic on the inputs; the volume response is indicative.
- Scope is the customers and SKUs present in the offer file, within Modern Formats and E-Commerce.
