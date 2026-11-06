# HeritageEdge: context for AI assistants

Flask app for the MRF (Heritage Foods, dairy FMCG) team. Humans: `README.md`. **Start with `docs/STATUS.md`** (state, what is left, how we work). Figures are monthly run-rates in rupees; with no real data built it runs on synthetic `sample_data/`.

Screens: `/` promo simulator (levers, scope, KPIs, breakeven, volume ladder, bridge, per-row adjustments); `/pnl` P&L (net revenue to net margin, any grouping, drill-down, Excel); `/data` build the dataset from SQL volumes + offer file + MIS (admin only); `/admin/users` and `/admin/activity` (admin only); login pages.

## Confidential data: the one rule

Real chain-wise prices, offers, volumes and costs must never reach git.
- Git-ignored: `data/` (dataset, uploads, SQL cache, `users.db` accounts and activity log, `login_chains.txt`, `secret.key`), `.env`, `*.xlsb`, `*.xlsx`. Safe: `sample_data/`, `config/`, `engine/sql/billing.sql` (table names only).
- Never put real chain names, prices, volumes or costs in code, tests, docs, commits or screenshots. Tests use `tests/synth.py`; example emails are `example.com`.
- Never print or log `SQL_PASSWORD` (`sql_source._clean()` scrubs it) or `SMTP_PASSWORD`. Set-password links go only to the admin or the person they belong to.
- `app.py` will not listen beyond this computer until an admin exists (`python manage.py create-admin`).

## Commands

`pip install -r requirements.txt`, `python app.py` (http://localhost:5000), `python -m pytest -q` before every commit. SQL Server needs `pyodbc` and the ODBC driver; copy `.env.example` to `.env`.

## Architecture

- `engine/data.py`: loads `data/rows.csv` else `sample_data/rows.csv`; one row per chain x customer group x sales office x SKU; dimensions in `DIM_DEFS`.
- `engine/sql_source.py` + `sql/billing.sql` (output columns are a tested contract) -> `data/billing.csv`; `engine/prepare.py` builds `rows.csv` from billing + offer file + MIS and writes `meta.json` (quality report, MIS reconciliation); `engine/datastore.py` runs uploads and jobs.
- `engine/simulate.py`: all maths (`calc_row`, `simulate`, `pnl`, `bridge`); `Levers` frozen dataclass, `LIMITS` clamps input; functions take `allowed` rows; `strip_below_cm` and `BELOW_CONTRIBUTION` for KAMs.
- `engine/export.py` Excel; `engine/users.py` accounts, tokens, lockout, KAM scope, aliases; `engine/access.py` KAM sheet import and `allowed_indices`; `engine/mailer.py`; `engine/network.py` login picture; `engine/activity.py` activity log.
- `app.py`: thin routes, login, CSRF, `log(...)`. Keep logic in `engine/`.
- `config/assumptions.csv` elasticities (volume suggestion only; volume is a user input); `config/attribution.csv` spreads each MIS cost per pack or as % of revenue.
- Front end: plain JS, one CSS file, no build. `static/js/common.js` has lever defs (`GROUPS`), presets, selectors, formatting, `window.HE`.

## Conventions

- UI copy: plain, specific, sentence case; no all-caps labels, no arrows on buttons. Money in crore (Cr) and lakh (L); tables in rupees lakh.
- Colour carries meaning: green good, red bad; cost lines use inverted colouring (`goodUp` in `simulator.js`, `pnl.js`).
- Roles: admin; leadership (all data); KAM (own state + chain rows, P&L to contribution only). A route that returns numbers gets `allowed` rows and strips below-contribution keys for a KAM; Data and Users are admin only. A route that exports data or changes access calls `log(...)`. The activity log holds events only: never numbers, lever values, chosen chain names or passwords.
- The simulator reports the selected rows only and every lever applies to them. On the P&L the cost levers (COGS, S&D) apply to all rows; other levers respect scope. Row adjustments (`adjust` in levers) add the five commercial levers per chain, category, SKU or state.
- Contribution = gross margin - over/under - S&D (MIS "MC"). Net margin = contribution - P&A - interest - marketing.
- Every maths change needs a test (`tests/test_engine.py`, or `test_prepare.py`) and a `docs/HOW_IT_WORKS.md` update. Adding a lever touches engine, `common.js`, `export.py`, tests, docs (see `CONTRIBUTING.md`).
- CSS rules must be scoped to their page; an unscoped table rule once broke the P&L first column.
- Form POSTs need the CSRF token; `/api/*` JSON relies on SameSite=Lax.

## Gotchas

- MIS is cut to the base months (`pick_mis_months`); never average MIS costs over months billing does not cover (once overstated net margin by ~3 points). Month column found by header or `MIS_MONTH_COLUMN`.
- The contribution bridge holds volume at baseline for rate steps; the volume entered is the remainder; it must reconcile to the scenario total (tested).
- "Other deductions and display fees" is the gap between list invoice and billed price (credit notes, mix, schemes), not only fees.
- Scenarios are saved in browser `localStorage`, not shared.
- Billing SQL misses pack formats like `700+700 ML`; `prepare.py` recomputes them. Volume units: ML/L as litres, GM/KG as kg, never converted.
- Google Fonts load from a CDN; falls back to system fonts offline.
- Flask dev server only; do not expose it as shared production hosting.

## More

`docs/STATUS.md` (handoff), `docs/HOW_IT_WORKS.md` (formulas, who sees what, activity log), `docs/DATA.md` (pipeline), `docs/DECISIONS.md` (why, open questions), `docs/ROADMAP.md` (next).
