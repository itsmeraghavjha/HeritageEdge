# HeritageEdge

A planning tool for the MRF team at Heritage Foods. It has two parts:

- **Promo simulator** (`/`): change price, promo, chain terms, demand response and costs, and see the effect on net revenue, gross margin, contribution and volume against a baseline. A contribution bridge shows how much each lever group adds or takes away.
- **P&L statement** (`/pnl`): gross sales to contribution, cut by chain, state, sales office and SKU, in any order, with drill-down. View baseline, scenario or the change between them, and export to Excel.

The data in this version is **sample data** for modern-trade dairy. Figures are monthly run-rates.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py        # http://localhost:5000
pytest               # engine tests
```

## How the numbers work

For every chain x sales office x SKU line:

```
MRP value
- trade margin            chain share of MRP
= Gross sales             invoice value
- promo
- rebates                 display, listing and other fees
= Net revenue
- COGS                    milk-linked share moves with milk cost
= Gross margin
- Logistics               per-unit freight x state factor
- Sales and distribution  3.5% of net revenue
= Contribution
```

Volume responds to the levers as follows:

- **Price (MRP) change:** each SKU's own price elasticity, scaled by the "Price sensitivity" lever.
- **Promo:** the part passed to shelf price acts on volume with a stronger response than a permanent price change (`PROMO_RESPONSE_BOOST`).
- **Trade margin and rebates:** more margin or display fees pushes extra volume (`PUSH_PER_PP_TRADE`, `PUSH_PER_PP_REBATE`).
- **Extra volume from visibility:** a manual uplift you set yourself.
- **Costs:** milk and freight changes apply to every line. All other levers can be limited to chosen chains, states and SKUs.

The contribution bridge applies lever groups one at a time with volume held at baseline, so each bar is a pure rate effect. The remainder is the volume response.

All assumptions live in `engine/data.py` (chains, SKUs, prices, costs, elasticities) and the constants at the top of `engine/simulate.py`.

## Using real data

Replace `build_rows()` in `engine/data.py` with a loader that returns rows shaped as
`{"chain", "state", "so", "sku", "units"}` (baseline monthly units), and update the chain, SKU and state tables with actual prices, costs and terms.

## Layout

```
app.py             Flask routes and JSON API
engine/            data, simulation, Excel export
templates/         simulator and P&L pages
static/            CSS and JavaScript
tests/             engine tests
```
