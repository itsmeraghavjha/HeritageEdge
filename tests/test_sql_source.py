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
    assert sql_source.marker_count(sql) == 1, "exactly one ?, for the first billing date"
    assert "--" not in sql, "comments are removed before the query is sent"
    final = sql[sql.rindex("SELECT"):sql.index("FROM base")]
    missing = [c for c in prepare.BILLING_REQUIRED if c not in final]
    assert not missing, f"the billing query no longer outputs: {missing}"


def test_the_query_file_holds_no_login():
    text = sql_source.strip_comments(Path(sql_source.SQL_DIR / "billing.sql").read_text()).lower()
    for word in ("password", "pwd=", "uid="):
        assert word not in text
