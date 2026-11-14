"""SQL connection settings and the billing query. No real server is used: pyodbc is never called."""
import re
from pathlib import Path

import pytest

from engine import prepare, sql_source


def settings(monkeypatch, **kw):
    base = {"SQL_SERVER": "db.example.test", "SQL_DATABASE": "Demo", "SQL_USER": "reader", "SQL_PASSWORD": "pw"}
    for k, v in {**base, **kw}.items():
        monkeypatch.setenv(k, v)


def test_server_without_a_port_is_used_as_written(monkeypatch):
    settings(monkeypatch)
    assert "SERVER=db.example.test;" in sql_source.connection_string()


def test_sql_port_is_added_to_the_server(monkeypatch):
    settings(monkeypatch, SQL_PORT="1433")
    assert "SERVER=db.example.test,1433;" in sql_source.connection_string()


def test_a_port_already_in_the_server_wins(monkeypatch):
    settings(monkeypatch, SQL_SERVER="db.example.test,1500", SQL_PORT="1433")
    cs = sql_source.connection_string()
    assert "SERVER=db.example.test,1500;" in cs and "1433" not in cs


@pytest.mark.parametrize("bad", ["abc", "0", "70000", "14 33", "-1"])
def test_a_bad_port_is_named_as_the_problem(monkeypatch, bad):
    settings(monkeypatch, SQL_PORT=bad)
    with pytest.raises(sql_source.SqlError, match="SQL_PORT"):
        sql_source.connection_string()


def test_password_with_special_characters_is_braced_and_never_shown(monkeypatch):
    settings(monkeypatch, SQL_PASSWORD="pa;ss}word=1")
    cs = sql_source.connection_string()
    assert "PWD={pa;ss}}word=1};" in cs
    assert "pa;ss}word=1" not in sql_source._clean("login failed for pa;ss}word=1")


def test_missing_settings_are_listed_and_port_is_optional(monkeypatch):
    for k in sql_source.REQUIRED_SETTINGS:
        monkeypatch.setenv(k, "")
    assert sql_source.missing_settings() == list(sql_source.REQUIRED_SETTINGS)
    settings(monkeypatch)
    monkeypatch.setenv("SQL_PORT", "")
    assert sql_source.missing_settings() == []


def test_a_question_mark_in_a_comment_or_a_string_is_not_a_parameter():
    sql = "-- is this ok?\nSELECT 'why?' AS q /* maybe? */ FROM t WHERE d >= ? -- trailing?"
    clean = sql_source.strip_comments(sql)
    assert "ok?" not in clean and "maybe" not in clean and "trailing" not in clean
    assert "'why?'" in clean, "text inside quotes is kept"
    assert sql_source.marker_count(clean) == 1


def test_the_billing_query_is_shared_and_gives_the_columns_the_build_needs(monkeypatch):
    monkeypatch.setenv("SQL_QUERY_FILE", "")
    sql = sql_source.read_query()
    assert sql_source.marker_count(sql) == 2, "two ?: the first billing date and the day after the last"
    assert "--" not in sql, "comments are removed before the query is sent"
    final = sql[sql.rindex("SELECT"):sql.index("FROM base")]
    missing = [c for c in prepare.BILLING_REQUIRED if c not in final]
    assert not missing, f"the billing query no longer outputs: {missing}"


def test_the_query_file_holds_no_login():
    text = sql_source.strip_comments(Path(sql_source.SQL_DIR / "billing.sql").read_text()).lower()
    for word in ("password", "pwd=", "uid="):
        assert word not in text


# ---------- the From and To dates ----------
from datetime import date


class FakeCursor:
    description = [("BillMonth",), ("Qty_LtrKg",)]

    def __init__(self, log):
        self.log, self.sent = log, False

    last_day = date(2026, 8, 30)       # what the latest-billing-day query finds; None means it found nothing

    def execute(self, sql, *params):
        self.asked_last = "MAX(" in sql
        if not self.asked_last:
            self.log.append(params)

    def fetchone(self):
        return (self.last_day,)

    def fetchmany(self, n):
        if self.sent:
            return []
        self.sent = True
        return [(date(2026, 8, 1), 10.0)]


class FakeConn:
    timeout = 0

    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def close(self):
        pass


def fake_pull(monkeypatch, tmp_path, query):
    from engine import data
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sql_source, "read_query", lambda: query)
    log = []
    monkeypatch.setattr(sql_source, "connect", lambda: FakeConn(log))
    return log


TWO = "SELECT 1 WHERE d >= ? AND d < ?"


def test_the_to_date_is_included_so_the_query_gets_the_next_day(monkeypatch, tmp_path):
    log = fake_pull(monkeypatch, tmp_path, TWO)
    out = sql_source.fetch_billing("2026-08-01", end="2026-08-31")
    assert log == [(date(2026, 8, 1), date(2026, 9, 1))] and out["end"] == "2026-08-31" and out["rows"] == 1


def test_no_to_date_means_everything_from_the_start(monkeypatch, tmp_path):
    log = fake_pull(monkeypatch, tmp_path, TWO)
    out = sql_source.fetch_billing("2026-08-01")
    assert log[0][0] == date(2026, 8, 1) and log[0][1].year == 2999 and out["end"] is None


def test_a_query_with_one_marker_still_takes_only_a_from_date(monkeypatch, tmp_path):
    log = fake_pull(monkeypatch, tmp_path, "SELECT 1 WHERE d >= ?")
    sql_source.fetch_billing("2026-08-01")
    assert log == [(date(2026, 8, 1),)]


def test_a_to_date_needs_a_query_that_can_use_it(monkeypatch, tmp_path):
    fake_pull(monkeypatch, tmp_path, "SELECT 1 WHERE d >= ?")
    with pytest.raises(sql_source.SqlError, match="From date only"):
        sql_source.fetch_billing("2026-08-01", end="2026-08-31")


def test_a_to_date_before_the_from_date_is_refused(monkeypatch, tmp_path):
    fake_pull(monkeypatch, tmp_path, TWO)
    with pytest.raises(sql_source.SqlError, match="before the From"):
        sql_source.fetch_billing("2026-08-31", end="2026-08-01")


def test_three_markers_are_refused(monkeypatch, tmp_path):
    fake_pull(monkeypatch, tmp_path, "SELECT 1 WHERE a = ? AND b = ? AND c = ?")
    with pytest.raises(sql_source.SqlError, match="two at most"):
        sql_source.fetch_billing("2026-08-01")


def test_a_bad_date_names_which_one():
    with pytest.raises(sql_source.SqlError, match="To date is not a date"):
        sql_source.as_date("soon", "To")


def test_the_latest_billing_day_is_kept_with_the_pull(monkeypatch, tmp_path):
    fake_pull(monkeypatch, tmp_path, TWO)
    out = sql_source.fetch_billing("2026-08-01", end="2026-08-31")     # asked for the 31st, the database has the 30th
    assert out["last_billed"] == "2026-08-30" and out["end"] == "2026-08-31"


def test_a_failure_finding_the_latest_billing_day_does_not_fail_the_pull(monkeypatch, tmp_path):
    fake_pull(monkeypatch, tmp_path, TWO)
    monkeypatch.setattr(FakeCursor, "fetchone", lambda self: (_ for _ in ()).throw(RuntimeError("no access")))
    out = sql_source.fetch_billing("2026-08-01", end="2026-08-31")
    assert out["last_billed"] is None and out["rows"] == 1


def test_the_latest_billing_day_query_holds_no_login_and_takes_the_same_two_dates():
    text = (sql_source.SQL_DIR / "last_billing_date.sql").read_text(encoding="utf-8")
    assert sql_source.marker_count(sql_source.strip_comments(text)) == 2
    for word in ("PWD", "PASSWORD", "UID=", "SERVER="):
        assert word not in text.upper()
