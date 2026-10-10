# HeritageEdge: project context for AI assistants

AI coding assistants such as Claude Code read this file automatically. Humans: start with `README.md`.

## What this is

A Flask app for the MRF (Heritage Foods, dairy FMCG) team. Two screens:

- `/` Promo simulator: levers for price, promo, chain terms, demand response and costs, with KPIs, a contribution bridge and breakdowns.
- `/pnl` P&L statement: gross sales to contribution by chain, state, sales office and SKU, with drill-down, baseline / scenario / change views and Excel export.

Data is **sample data** until real actuals are loaded. Monthly run-rates, rupees.

## Commands

```bash
pip install -r requirements.txt
python app.py        # http://localhost:5000
pytest               # run before every commit
```

## Architecture

- `engine/data.py`: reference tables and `build_rows()` (one row per chain x sales office x SKU with baseline `units`). This is the only place that should change when real data arrives.
- `engine/simulate.py`: all the maths. `calc_row` computes one row's P&L; `simulate`, `pnl` and `bridge` aggregate. Levers are a frozen dataclass; `LIMITS` clamps API input.
- `engine/export.py`: Excel export (openpyxl).
- `app.py`: thin routes only. Keep logic in `engine/`.
- `static/js/common.js` defines lever definitions (`GROUPS`), defaults, presets and number formatting. The simulator and P&L pages share it through `window.HE`.
- No build step and no front-end framework. Plain JS, one CSS file.

## Conventions

- Plain, specific UI copy in sentence case. No all-caps labels, no arrows on buttons.
- Money is shown in Indian units: crore (Cr) and lakh (L). Tables use rupees lakh.
- Colour carries meaning: green is good for profit, red is bad. For cost lines (COGS, promo, rebates, logistics) an increase is bad, so those use inverted colouring (see the `goodUp` flag in `simulator.js` and `pnl.js`).
- Cost levers (milk, freight) apply to every row. All other levers respect the chain / state / SKU scope.
- Every maths change needs a test in `tests/test_engine.py` and an update to `docs/HOW_IT_WORKS.md`.

## Gotchas

- Adding a lever means editing the engine, `common.js`, `export.py`, the tests and the docs. See `CONTRIBUTING.md`.
- The contribution bridge holds volume at baseline for its rate steps; the volume response is the remainder. Keep it reconciling to the scenario total (a test checks this).
- Scenarios are saved in browser `localStorage`, not on the server, so they are not shared between users.
- Google Fonts load from a CDN. The app falls back to system fonts without network access.
- `app.py` runs Flask in debug mode for development only.

## Where to read more

`docs/HOW_IT_WORKS.md` (formulas), `docs/DATA.md` (real data), `docs/DECISIONS.md` (why and open questions), `docs/ROADMAP.md` (what next).
