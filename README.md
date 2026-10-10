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

## Documentation

Everything a new team member needs is in the repo:

| Read | To learn |
|---|---|
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | What each number and lever means, and how volume responds |
| [docs/DATA.md](docs/DATA.md) | How to replace the sample data with real data |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Why it was built this way, and the questions still open |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What is planned next |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Daily workflow, and how to add a lever, chain or SKU |
| [CLAUDE.md](CLAUDE.md) | Project context for AI assistants such as Claude Code |

## Using real data

See [docs/DATA.md](docs/DATA.md). In short: replace `build_rows()` in `engine/data.py` and update the chain, SKU and state tables.

## Layout

```
app.py             Flask routes and JSON API
engine/            data, simulation, Excel export
templates/         simulator and P&L pages
static/            CSS, JavaScript and images
tests/             engine tests
docs/              how it works, data guide, decisions, roadmap
```
