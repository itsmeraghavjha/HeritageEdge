# Working on HeritageEdge

## First time

```bash
git clone https://github.com/itsmeraghavjha/HeritageEdge.git
cd HeritageEdge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py                    # http://localhost:5000
```

Read in this order: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/DECISIONS.md`.

## Every day

```bash
git pull origin main             # get the latest first
# ... make your change ...
pytest                           # must pass
git add -A
git commit -m "Say what changed and why"
git push origin main
```

If several people work at once, use a branch and a pull request instead of pushing straight to `main`:

```bash
git checkout -b my-change
git push -u origin my-change     # then open a pull request on GitHub
```

## Rules of thumb

- **Run `pytest` before pushing.** The tests check that the P&L adds up and the bridge reconciles.
- **Change numbers in one place.** Assumptions live in `engine/data.py` and the constants at the top of `engine/simulate.py`. Do not hard-code figures in the pages.
- **Update the docs when behaviour changes.** If you change a formula, update `docs/HOW_IT_WORKS.md`. If you make a choice someone could question, add it to `docs/DECISIONS.md`.
- **Keep the sample data labelled as sample** until real data is loaded.

## Adding a new lever

A lever touches several places. Do all of them:

1. `engine/simulate.py`: add the field to `Levers`, its range to `LIMITS`, and use it in `calc_row`. If it should show in the bridge, add it to a group in `bridge()`.
2. `static/js/common.js`: add its default to `DEFAULTS` and its slider settings to `GROUPS`.
3. `engine/export.py`: add its label to `LEVER_LABELS` so it appears in the Excel export.
4. `tests/test_engine.py`: add a test for what it should do.
5. `docs/HOW_IT_WORKS.md`: add it to the lever table.

## Adding a chain, SKU, state or sales office

Add it to the matching table in `engine/data.py`. Rows only appear if `build_rows()` (or your real loader) returns volume for them.
