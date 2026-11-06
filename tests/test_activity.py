"""The activity log: who signed in, who exported, who changed access. Events only, never numbers or passwords."""
import json
import time

import pytest

import app as app_module
from engine import activity, data, mailer, users
from test_kam import login, post_json


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mailer, "send", lambda *a: (True, ""))
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(app_module, "_seen", {})
    admin = users.create("boss@example.com", "Boss", "admin", password="Boss-pass-12345")
    users.create("asha@example.com", "Asha", "leadership", password="Asha-pass-12345")
    return admin


def client(email, pw):
    c = app_module.app.test_client()
    assert login(c, email, pw).status_code == 302
    return c


def form(c, path, **data):
    with c.session_transaction() as s:
        s["csrf"] = "t"
    return c.post(path, data={"csrf": "t", **data})


def actions(**kw):
    events, _ = activity.recent(100, **kw)
    return [e["action"] for e in events]


def test_sign_in_failed_sign_in_and_sign_out_are_recorded(world):
    c = app_module.app.test_client()
    login(c, "asha@example.com", "wrong-password-1")
    c2 = client("asha@example.com", "Asha-pass-12345")
    form(c2, "/logout")
    got = actions()
    assert "login_failed" in got and "login" in got and "logout" in got
    failed = [e for e in activity.recent(100)[0] if e["action"] == "login_failed"][0]
    assert failed["email"] == "asha@example.com"


def test_a_password_typed_into_the_email_box_is_not_kept(world):
    c = app_module.app.test_client()
    login(c, "Hunter2-secret-pw", "whatever-1234")
    rows = activity.recent(10)[0]
    assert rows and all("Hunter2" not in (r["email"] + r["detail"]) for r in rows)
    assert rows[0]["email"] == "(not an email address)"


def test_screens_are_counted_once_per_visit_and_refused_pages_are_not_views(world):
    kam = users.create("ravi@example.com", "Ravi", "kam", password="Ravi-pass-12345")
    c = client("ravi@example.com", "Ravi-pass-12345")
    for _ in range(3):
        c.get("/pnl")
    assert c.get("/data").status_code == 403
    views = [e["detail"] for e in activity.recent(100, user_id=kam)[0] if e["action"] == "view"]
    assert views == ["pnl"]


def test_exports_and_access_changes_are_recorded_with_who_did_them(world):
    kam = users.create("ravi@example.com", "Ravi", "kam", password="Ravi-pass-12345")
    k = client("ravi@example.com", "Ravi-pass-12345")
    assert post_json(k, "/api/export", {}).status_code == 200
    a = client("boss@example.com", "Boss-pass-12345")
    form(a, f"/admin/users/{kam}/scope/add", state="Telangana", cluster="Sample Chain A")
    form(a, f"/admin/users/{kam}/active", active="0")
    events = activity.recent(100)[0]
    by = {e["action"]: e for e in events}
    assert by["export"]["email"] == "ravi@example.com" and "contribution" in by["export"]["detail"]
    assert by["mapping_added"]["email"] == "boss@example.com" and "ravi@example.com" in by["mapping_added"]["detail"]
    assert by["user_disabled"]["detail"] == "ravi@example.com"


def test_the_log_holds_no_numbers_from_the_request(world):
    a = client("boss@example.com", "Boss-pass-12345")
    post_json(a, "/api/export", {"levers": {"price_change_pct": 7.5}})
    blob = json.dumps([dict(e) for e in activity.recent(100)[0]])
    assert "7.5" not in blob and "price_change" not in blob


def test_only_the_admin_sees_the_activity_page_and_csv(world):
    lead = client("asha@example.com", "Asha-pass-12345")
    assert lead.get("/admin/activity").status_code == 403
    assert lead.get("/admin/activity.csv").status_code == 403
    a = client("boss@example.com", "Boss-pass-12345")
    page = a.get("/admin/activity")
    assert page.status_code == 200 and b"People active this week" in page.data
    csv = a.get("/admin/activity.csv")
    assert csv.mimetype == "text/csv" and csv.data.startswith(b"time,email,role,action,detail")


def test_filters_summary_and_pruning(world):
    asha = users.get_by_email("asha@example.com")
    client("asha@example.com", "Asha-pass-12345")
    assert set(actions(group="access")) == {"login"}
    assert actions(user_id=999) == []
    s = activity.summary()
    assert s["tiles"]["logins_7"] == 1 and s["tiles"]["active_7"] == 1
    assert s["people"][asha["id"]]["logins_30"] == 1
    # an old event is dropped the next time the log is pruned
    activity.record(asha, "login")
    from contextlib import closing
    with closing(activity._db()) as con:
        con.execute("UPDATE activity SET ts=?", (time.time() - 500 * 86400,))
        con.commit()
    activity._last_prune[0] = 0
    activity.record(asha, "logout")
    assert actions() == ["logout"]


def test_nothing_is_logged_before_logins_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    c = app_module.app.test_client()
    c.get("/pnl")
    post_json(c, "/api/export", {})
    assert activity.recent(10)[0] == []


def test_filter_use_counts_the_dimension_only_never_what_was_chosen(world):
    a = client("boss@example.com", "Boss-pass-12345")
    assert post_json(a, "/api/usage", {"filter": "state", "screen": "pnl", "values": ["Telangana"]}).status_code == 204
    post_json(a, "/api/usage", {"filter": "state", "screen": "simulator"})
    post_json(a, "/api/usage", {"filter": "chain", "screen": "simulator"})
    post_json(a, "/api/usage", {"filter": "<script>", "screen": "pnl"})      # not a real dimension: ignored
    post_json(a, "/api/usage", {"filter": "state", "screen": "elsewhere"})   # not a real screen: ignored
    events = [e for e in activity.recent(100)[0] if e["action"] == "filter"]
    assert sorted(e["detail"] for e in events) == ["pnl:state", "simulator:chain", "simulator:state"]
    assert "Telangana" not in json.dumps(events)
    rows = {f["name"]: f for f in activity.summary()["filters"]}
    assert rows["State"]["n"] == 2 and rows["State"]["where"] == {"P&L statement": 1, "Promo simulator": 1}
    assert rows["Chain"]["n"] == 1
    assert activity.describe("filter", "pnl:state") == "State, on P&L statement"
    page = a.get("/admin/activity").get_data(as_text=True)
    assert "Filters used" in page and "State" in page
