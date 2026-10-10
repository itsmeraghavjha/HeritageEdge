"""Pulls monthly billing volumes from SQL Server and caches them to data/billing.csv.

Credentials come from .env (SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD, ...). Use a read-only login.
The query is engine/sql/billing.sql (kept out of git, because it names your real tables; see billing.example.sql).
If it contains a single ?, that is filled with the first billing date to pull.
"""
import csv
import os
from datetime import date, datetime
from pathlib import Path

from . import data, env  # noqa: F401  (env loads .env on import)

SQL_DIR = Path(__file__).resolve().parent / "sql"
REQUIRED_SETTINGS = ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD")


class SqlError(Exception):
    """A problem with the SQL connection or query, in words a user can act on."""


def sql_file():
    return Path(os.environ.get("SQL_QUERY_FILE") or SQL_DIR / "billing.sql")


def read_query():
    path = sql_file()
    if not path.exists():
        raise SqlError(f"The billing query file is missing ({path.name}). Copy engine/sql/billing.example.sql to "
                       f"{path.name}, put your own table and column names in it, and keep the output column names.")
    return path.read_text(encoding="utf-8")


def billing_path():
    return data.DATA_DIR / "billing.csv"


def missing_settings():
    return [k for k in REQUIRED_SETTINGS if not os.environ.get(k)]


def _clean(message):
    """Never let a password reach the screen or a log."""
    pw = os.environ.get("SQL_PASSWORD")
    text = str(message)
    return text.replace(pw, "***") if pw else text


def _odbc(value):
    """Braces let a value contain ; = and spaces. A closing brace inside the value is doubled."""
    return "{" + str(value).replace("}", "}}") + "}"


def connection_string():
    driver = os.environ.get("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    trust = os.environ.get("SQL_TRUST_CERT", "yes")
    return (f"DRIVER={{{driver}}};SERVER={os.environ['SQL_SERVER']};DATABASE={os.environ['SQL_DATABASE']};"
            f"UID={_odbc(os.environ['SQL_USER'])};PWD={_odbc(os.environ['SQL_PASSWORD'])};"
            f"TrustServerCertificate={trust};")


def connect():
    missing = missing_settings()
    if missing:
        raise SqlError("SQL is not set up. Add these to your .env file: " + ", ".join(missing) + ".")
    try:
        import pyodbc
    except ImportError:
        raise SqlError("The pyodbc package is not installed. Run: pip install pyodbc "
                       "(you also need the Microsoft ODBC Driver for SQL Server on this computer).")
    try:
        return pyodbc.connect(connection_string(), timeout=30)
    except Exception as exc:  # pyodbc raises several error types
        raise SqlError("Could not connect to SQL Server: " + _clean(exc))


def test_connection():
    """Returns (ok, message)."""
    try:
        conn = connect()
        try:
            conn.cursor().execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return True, "Connected to SQL Server."
    except SqlError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, "SQL test failed: " + _clean(exc)


def default_start(today=None):
    """First day of the month six months back, e.g. 2026-04-01 when today is in October 2026."""
    today = today or date.today()
    months = today.year * 12 + today.month - 1 - 6
    return date(months // 12, months % 12 + 1, 1)


def fetch_billing(start, progress=None, fetch_size=20000):
    """Runs the billing query from `start` (a date) and writes data/billing.csv. Returns a summary dict."""
    if isinstance(start, str):
        start = datetime.strptime(start[:10], "%Y-%m-%d").date()
    sql = read_query()
    say = progress or (lambda *a, **k: None)

    say("Connecting to SQL Server", 0.05)
    conn = connect()
    path = billing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    rows = 0
    months = set()
    try:
        conn.timeout = 900
        cur = conn.cursor()
        say("Running the billing query. This can take a few minutes", 0.15)
        try:
            cur.execute(sql, start) if "?" in sql else cur.execute(sql)
        except Exception as exc:
            raise SqlError("The billing query failed: " + _clean(exc))
        cols = [c[0] for c in cur.description]
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            idx = cols.index("BillMonth") if "BillMonth" in cols else None
            while True:
                batch = cur.fetchmany(fetch_size)
                if not batch:
                    break
                for r in batch:
                    w.writerow(["" if v is None else (v.isoformat() if hasattr(v, "isoformat") else v) for v in r])
                    if idx is not None and r[idx] is not None:
                        months.add(str(r[idx])[:10])
                rows += len(batch)
                say(f"Downloaded {rows:,} rows", 0.15 + min(0.8, rows / 2_000_000))
    finally:
        conn.close()
    if rows == 0:
        tmp.unlink(missing_ok=True)
        raise SqlError("The query returned no rows. Check the start date and your access to the billing tables.")
    os.replace(tmp, path)
    say("Saved billing volumes", 1.0)
    return {"rows": rows, "months": sorted(months), "start": start.isoformat(),
            "pulled_at": datetime.now().isoformat(timespec="seconds")}
