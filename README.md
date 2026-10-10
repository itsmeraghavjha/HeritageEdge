# HeritageEdge

A planning tool for the MRF team at Heritage Foods.

- **Promo simulator** (`/`): change price, promo, chain terms, demand response and costs, and see the effect on net revenue, gross margin, contribution and volume against a baseline. A bridge chart shows how much each lever group adds or takes away.
- **P&L statement** (`/pnl`): net revenue down to net margin, grouped in any order by chain, customer group, state, city, sales office, product category, variant, pack size and SKU. Filter, drill down, compare baseline with scenario, and export to Excel.
- **Data** (`/data`): pull volumes from SQL Server, upload the offer file and MIS workbook, and build the dataset the other screens use.

Figures are monthly run-rates in rupees. Until a real dataset is built, the app runs on **synthetic sample data**.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py                    # http://localhost:5000
pytest                           # tests
```

## Use your real data

Real data never goes into git. It stays on the computer running the app.

1. `pip install pyodbc` and install the Microsoft ODBC Driver for SQL Server.
2. Copy `.env.example` to `.env` and fill in the SQL Server details. Use a read-only login.
3. Copy `engine/sql/billing.example.sql` to `engine/sql/billing.sql` and use your own table names (or ask a teammate for their copy).
4. Open **Data**: test the connection, pull billing from SQL, upload the offer file and MIS workbook, tick the baseline months and build.
5. Check the reconciliation shown after the build before trusting the P&L.

Set `APP_PASSWORD` in `.env` to require a login. This is strongly recommended on any shared computer, and required to listen beyond your own machine.

Details: [docs/DATA.md](docs/DATA.md).

## Documentation

| Read | To learn |
|---|---|
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | What each number and lever means, and how volume responds |
| [docs/DATA.md](docs/DATA.md) | How the dataset is built from SQL, the offer file and the MIS |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Why it was built this way, and the questions still open |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What is planned next |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Daily workflow, and how to add a lever or dimension |
| [CLAUDE.md](CLAUDE.md) | Project context for AI assistants such as Claude Code |

## Layout

```
app.py                 Flask routes and optional login
engine/                maths, data loading, SQL pull, dataset build, Excel export
config/                elasticities, milk share, cost attribution rules (safe to share)
sample_data/           synthetic dataset used when no real data is built
templates/ static/     pages, scripts and styles
tests/                 tests, including a synthetic data generator
data/                  real data (ignored by git, never commit)
```
