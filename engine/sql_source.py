"""Pulls monthly billing volumes from SQL Server and caches them to data/billing.csv.

Credentials come from .env (SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD, ...). Use a read-only login.
The query is engine/sql/billing.sql. It is in git because it holds table names only; the login stays in .env.
If it contains a single ?, that is filled with the first billing date to pull.
"""
import csv
import os
from datetime import date, datetime
from pathlib import Path

from . import data, env  # noqa: F401  (env loads .env on import)

SQL_DIR = Path(__file__).resolve().parent / "sql"
REQUIRED_SETTINGS = ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD")  # SQL_PORT is optional


class SqlError(Exception):
    """A problem with the SQL connection or query, in words a user can act on."""


def sql_file():
    return Path(os.environ.get("SQL_QUERY_FILE") or SQL_DIR / "billing.sql")


def strip_comments(sql):
    """Removes -- and /* */ comments, leaving quoted text alone. A ? in a comment must never be mistaken for a
    parameter, so the query is cleaned before it is run or counted."""
    out, i, n, in_str = [], 0, len(sql), False
    while i < n:
        ch = sql[i]
        if in_str:
            out.append(ch)
            if ch == "'":
                if sql[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            out.append(ch)
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def marker_count(sql):
    """Number of ? parameter markers outside quoted text, in a comment-free query."""
    count, in_str = 0, False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        elif ch == "?" and not in_str:
            count += 1
    return count


def read_query():
    path = sql_file()
    if not path.exists():
        raise SqlError(f"The billing query file is missing ({path.name}). Run git pull to get engine/sql/billing.sql, "
                       f"or point SQL_QUERY_FILE in .env at your own query.")
    return strip_comments(path.read_text(encoding="utf-8")).strip()


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


def server_address():
    """SQL_SERVER, plus ",port" when SQL_PORT is set. A port already written in SQL_SERVER (host,1433) wins."""
    server = os.environ["SQL_SERVER"].strip()
    port = os.environ.get("SQL_PORT", "").strip()
    if not port or "," in server:
        return server
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise SqlError("SQL_PORT in .env must be a number between 1 and 65535, for example 1433.")
    return f"{server},{port}"


def connection_string():
    driver = os.environ.get("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    trust = os.environ.get("SQL_TRUST_CERT", "yes")
    return (f"DRIVER={{{driver}}};SERVER={server_address()};DATABASE={os.environ['SQL_DATABASE']};"
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
    conn_str = connection_string()  # a bad setting is reported as itself, not as a failed connection
    try:
        return pyodbc.connect(conn_str, timeout=30)
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
    if marker_count(sql) > 1:
        raise SqlError(f"The billing query has {marker_count(sql)} question marks. It should have one at most, "
                       "standing for the first billing date.")
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
            cur.execute(sql, start) if marker_count(sql) == 1 else cur.execute(sql)
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
