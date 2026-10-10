# How HeritageEdge works

This explains what the numbers mean and how the levers move them. It is written for both the MRF team and developers.

## What the tool is for

The MRF team wants to try a promo, price change or change in chain terms **before** committing to it, and see the effect on net revenue, margin and contribution. The simulator does that. The P&L page shows the same numbers as a statement, by chain, state, sales office and SKU.

Everything is a **monthly run-rate** for modern-trade chains. "Baseline" is today's position with no levers set. "Scenario" is the baseline with your levers applied.

## The P&L, line by line

Calculated for every line of data (one chain, in one sales office, for one SKU), then added up:

| Line | Meaning |
|---|---|
| MRP value | units x MRP |
| minus Trade margin | the chain's share of MRP |
| **= Gross sales** | invoice value billed to the chain |
| minus Promo | scheme or discount, as a % of invoice value |
| minus Rebates | display, listing and other fees, as a % of invoice value |
| **= Net revenue** | |
| minus COGS | per-unit cost. The milk-linked part moves with the milk cost lever |
| **= Gross margin** | |
| minus Logistics | per-unit freight x a state factor (further states cost more) |
| minus Sales and distribution | 3.5% of net revenue |
| **= Contribution** | what is left to cover overheads and profit |

## The levers

| Lever | What it changes | Applies to |
|---|---|---|
| MRP change (%) | Shelf price and invoice price together | Selected scope |
| Extra promo discount (pp) | Added to each chain's current promo rate | Selected scope |
| Promo passed to shelf price (%) | How much of the promo reaches shoppers and so drives volume | Selected scope |
| Trade margin change (pp) | Chain margin as a share of MRP | Selected scope |
| Rebates and display fees (pp) | Fees as a share of invoice value | Selected scope |
| Price sensitivity (x) | Scales every SKU's elasticity. 1x uses the built-in values | Selected scope |
| Extra volume from visibility (%) | Volume you expect from display or activation, on top of price effects | Selected scope |
| Milk procurement cost (%) | COGS, in proportion to each SKU's milk content | **Everything** |
| Freight cost (%) | Logistics cost per unit | **Everything** |

"Selected scope" means the chains, states and SKUs ticked under *Apply price, promo and chain terms to*. If nothing is ticked, all lines are included. The two cost levers always apply to everything, because a milk price rise does not skip a chain.

## How volume responds

Volume change for a line is the product of four effects:

1. **Price:** `(1 + price change) ^ elasticity`. Each SKU has its own elasticity, for example milk about -0.35 and cheese about -1.5.
2. **Promo:** the shelf-price drop from the promo, raised to `elasticity x PROMO_RESPONSE_BOOST` (currently 3). Shoppers react harder to a temporary promo than to a permanent price change.
3. **Chain terms:** more trade margin or display fees pushes extra volume (0.8% per +1 pp trade margin, 0.4% per +1 pp rebate).
4. **Visibility:** the "extra volume" lever, applied directly.

The constants are at the top of `engine/simulate.py`. **They are assumptions, not measurements.** See `DECISIONS.md`.

## The contribution bridge

The chart on the simulator page answers "which of my changes is doing what?". It applies lever groups one at a time (price, then promo, then trade terms, then costs) **with volume held at baseline**, so each bar is a pure rate effect. Whatever remains is the **volume response**. The bars always add up exactly to the scenario contribution.

## Where things live

```
app.py                  Flask routes and JSON API
engine/data.py          Chains, states, sales offices, SKUs and baseline volumes
engine/simulate.py      P&L maths, levers, bridge, grouping
engine/export.py        Excel export
templates/              Page layouts (simulator, P&L, shared base)
static/js/common.js     Lever definitions, presets, number formatting, sidebar
static/js/simulator.js  Simulator page
static/js/pnl.js        P&L page
static/css/app.css      All styling
tests/test_engine.py    Engine tests
```

API (all JSON, all POST except meta): `/api/meta`, `/api/simulate`, `/api/pnl`, `/api/export`. Each takes `levers`, `scope` and, for P&L and export, `dims`.

## Known limits

- Scenarios you save are stored **in your own browser**, so they are not shared with the team yet.
- There is no login.
- `python app.py` runs Flask's development server. Do not use it as a shared production deployment.
- The data is sample data. See `DATA.md`.
