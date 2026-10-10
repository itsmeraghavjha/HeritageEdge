# HeritageEdge: project context for AI assistants

AI coding assistants such as Claude Code read this file automatically. Humans: start with `README.md`.

## What this is

A Flask app for the MRF (Heritage Foods, dairy FMCG) team. Three screens:

- `/` Promo simulator: levers for price, promo, chain terms, demand response and costs, with KPIs, a contribution bridge and breakdowns. The "apply to" scope can be limited on any dimension.
- `/pnl` P&L statement: net revenue down to net margin, grouped in any order by chain, customer group, state, city, sales office, product category, variant, pack size and SKU, with filters, drill-down, baseline / scenario / change views and Excel export.
- `/data` Data: pulls monthly volumes from SQL Server, takes the offer file and MIS workbook as uploads, and builds the dataset the other two screens run on.

Everything is a monthly run-rate in rupees. With no real data built, the app runs on synthetic **sample data**.

## Confidential data: the one rule that matters

The real dataset holds chain-wise prices, offers and costs. It must never reach git.

- Ignored by git: `data/` (built dataset, uploads, cached SQL pull), `.env` (SQL login, app password), `engine/sql/billing.sql` (real table names), `*.xlsb`, `*.xlsx`.
- Safe in git: `sample_data/`, `config/` (elasticities, milk share, cost attribution rules), `engine/sql/billing.example.sql`.
- Never put real chain names, prices, volumes or costs in code, tests, docs, commit messages or screenshots. Tests use `tests/synth.py`.
- Never print or log `SQL_PASSWORD`. `sql_source._clean()` scrubs it from errors.
- `app.py` refuses to listen beyond this computer unless `APP_PASSWORD` is set.

## Commands

```bash
pip install -r requirements.txt
python app.py        # http://localhost:5000
pytest               # run before every commit
```

SQL Server also needs `pip install pyodbc` and the Microsoft ODBC driver. Copy `.env.example` to `.env` and fill it in.

## Architecture

- `engine/data.py`: loads `data/rows.csv` if a real dataset has been built, otherwise `sample_data/rows.csv`. One row per chain x customer group x sales office x SKU with baseline `units`, terms and attributed costs. Defines the dimensions (`DIM_DEFS`).
- `engine/sql_source.py` + `engine/sql/billing.sql`: pulls billing volumes from SQL Server into `data/billing.csv`. The query's output column names are a contract; `billing.example.sql` lists them.
- `engine/prepare.py`: builds `rows.csv` from billing + offer file + MIS. Averages the chosen base months, takes terms from the offer file, spreads MIS costs to chains, and writes `meta.json` with a quality report and a reconciliation against the MIS.
- `engine/datastore.py`: uploads and background jobs for the Data page.
- `engine/simulate.py`: all the maths. `calc_row` computes one row's P&L; `simulate`, `pnl` and `bridge` aggregate. Levers are a frozen dataclass; `LIMITS` clamps API input.
- `engine/export.py`: Excel export (openpyxl).
- `app.py`: thin routes and the optional login. Keep logic in `engine/`.
- `config/assumptions.csv`: elasticity and milk share per product family. `config/attribution.csv`: whether each MIS cost is spread per pack (`volume`) or as a % of net revenue (`revenue`).
- `static/js/common.js` defines lever definitions (`GROUPS`), defaults, presets, selectors and number formatting, shared through `window.HE`.
- No build step and no front-end framework. Plain JS, one CSS file.

## Conventions

- Plain, specific UI copy in sentence case. No all-caps labels, no arrows on buttons.
- Money is shown in Indian units: crore (Cr) and lakh (L). Tables use rupees lakh.
- Colour carries meaning: green is good for profit, red is bad. For cost lines (COGS, promo, rebates, S&D, P&A, interest, over/under, marketing) an increase is bad, so those use inverted colouring (see the `goodUp` flag in `simulator.js` and `pnl.js`).
- Cost levers (milk, S&D / freight) apply to every row. All other levers respect the scope.
- Contribution is gross margin less S&D. Net margin is contribution less P&A, interest, over/under and marketing.
- Every maths change needs a test in `tests/test_engine.py` (or `test_prepare.py` for the build) and an update to `docs/HOW_IT_WORKS.md`.

## Gotchas

- Adding a lever means editing the engine, `common.js`, `export.py`, the tests and the docs. See `CONTRIBUTING.md`.
- The contribution bridge holds volume at baseline for its rate steps; the volume response is the remainder. Keep it reconciling to the scenario total (a test checks this).
- "Rebates and display fees" moves the gap between list invoice and billed price (credit notes, mix, other schemes), not only fees. See `docs/HOW_IT_WORKS.md`.
- Scenarios are saved in browser `localStorage`, not on the server, so they are not shared between users.
- The billing query's pack-size parsing misses formats like `700+700 ML`; `prepare.py` recomputes packs in Python for those lines.
- Google Fonts load from a CDN. The app falls back to system fonts without network access.
- `app.py` runs Flask's development server. Do not expose it as a shared production deployment.

## Where to read more

`docs/HOW_IT_WORKS.md` (formulas), `docs/DATA.md` (data pipeline), `docs/DECISIONS.md` (why and open questions), `docs/ROADMAP.md` (what next).
