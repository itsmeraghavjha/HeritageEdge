"""Login, set-password links and the admin's user management. Uses a throwaway data folder per test."""
import re

import pytest

import app as app_module
from engine import data, mailer, users


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: (sent.append((to, subject, body)) or (True, "")))
    monkeypatch.setattr(mailer, "configured", lambda: True)
    c = app_module.app.test_client()
    c.sent = sent
    return c


def csrf(c):
    c.get("/login")
    with c.session_transaction() as s:
        s["csrf"] = "t"
    return "t"


def make_admin(c, email="boss@example.com", pw="Secret-pass-123"):
    uid = users.create(email, "Boss", role="admin")
    tok = users.issue_token(uid, "invite")
    users.set_password_with_token(tok, pw)
    return uid


def login(c, email, pw):
    t = csrf(c)
    return c.post("/login", data={"email": email, "password": pw, "csrf": t})


def test_open_until_the_first_user_exists(client):
    assert client.get("/").status_code == 200
    make_admin(client)
    assert client.get("/").status_code == 302          # now needs a login
    assert client.post("/api/simulate", json={}).status_code == 401


def test_more_than_one_admin_is_allowed_but_an_admin_cannot_be_disabled(client):
    make_admin(client)
    uid = users.create("second@example.com", role="admin")
    with pytest.raises(users.UserError):
        users.set_active(uid, False)


def test_login_and_wrong_password(client):
    make_admin(client)
    assert "not right" in login(client, "boss@example.com", "wrong-pass-123").get_data(as_text=True)
    assert "not right" in login(client, "nobody@example.com", "whatever12345").get_data(as_text=True)
    assert login(client, "BOSS@example.com", "Secret-pass-123").status_code == 302
    assert client.get("/").status_code == 200


def test_lockout_after_repeated_failures(client):
    make_admin(client)
    for _ in range(users.MAX_FAILS):
        login(client, "boss@example.com", "wrong-pass-123")
    page = login(client, "boss@example.com", "Secret-pass-123").get_data(as_text=True)
    assert "Too many wrong attempts" in page


def test_admin_adds_user_who_sets_password_once(client):
    make_admin(client)
    login(client, "boss@example.com", "Secret-pass-123")
    t = csrf(client)
    client.post("/admin/users/new", data={"email": "asha@example.com", "name": "Asha", "csrf": t})
    to, subject, body = client.sent[-1]
    assert to == "asha@example.com"
    path = re.search(r"/set-password/\S+", body).group(0)

    user_client = app_module.app.test_client()
    assert user_client.get(path).status_code == 200
    t2 = csrf(user_client)
    bad = user_client.post(path, data={"password": "short", "again": "short", "csrf": t2}).get_data(as_text=True)
    assert "at least" in bad
    mismatch = user_client.post(path, data={"password": "Longer-pass-1", "again": "Longer-pass-2", "csrf": t2})
    assert "not the same" in mismatch.get_data(as_text=True)
    ok = user_client.post(path, data={"password": "Longer-pass-1", "again": "Longer-pass-1", "csrf": t2})
    assert ok.status_code == 302
    assert user_client.get(path).status_code == 410     # the link works once
    assert login(user_client, "asha@example.com", "Longer-pass-1").status_code == 302


def test_disabled_user_is_locked_out_at_once(client):
    make_admin(client)
    uid = users.create("asha@example.com", "Asha")
    users.set_password_with_token(users.issue_token(uid, "invite"), "Longer-pass-1")
    other = app_module.app.test_client()
    login(other, "asha@example.com", "Longer-pass-1")
    assert other.get("/").status_code == 200
    users.set_active(uid, False)
    assert other.get("/").status_code == 302
    assert "disabled" in login(other, "asha@example.com", "Longer-pass-1").get_data(as_text=True)
    users.set_active(uid, True)
    assert login(other, "asha@example.com", "Longer-pass-1").status_code == 302


def test_admin_cannot_be_disabled(client):
    uid = make_admin(client)
    with pytest.raises(users.UserError):
        users.set_active(uid, False)


def test_regular_user_cannot_open_admin_page(client):
    make_admin(client)
    uid = users.create("asha@example.com")
    users.set_password_with_token(users.issue_token(uid, "invite"), "Longer-pass-1")
    login(client, "asha@example.com", "Longer-pass-1")
    assert client.get("/admin/users").status_code == 403
    t = csrf(client)
    assert client.post("/admin/users/new", data={"email": "x@example.com", "csrf": t}).status_code == 403


def test_forgot_password_gives_the_same_answer_and_a_new_link_cancels_the_old(client):
    make_admin(client)
    t = csrf(client)
    known = client.post("/forgot", data={"email": "boss@example.com", "csrf": t}).get_data(as_text=True)
    unknown = client.post("/forgot", data={"email": "nobody@example.com", "csrf": t}).get_data(as_text=True)
    assert "on its way" in known and "on its way" in unknown
    assert len(client.sent) == 1
    first = re.search(r"/set-password/(\S+)", client.sent[0][2]).group(1)
    client.post("/forgot", data={"email": "boss@example.com", "csrf": t})
    assert users.token_user(first) is None


def test_form_without_csrf_token_is_refused(client):
    make_admin(client)
    assert client.post("/login", data={"email": "boss@example.com", "password": "Secret-pass-123"}).status_code == 400


def test_email_failure_shows_the_link_to_the_admin(client, monkeypatch):
    make_admin(client)
    login(client, "boss@example.com", "Secret-pass-123")
    monkeypatch.setattr(mailer, "send", lambda *a: (False, "The email could not be sent: no server"))
    t = csrf(client)
    client.post("/admin/users/new", data={"email": "asha@example.com", "csrf": t})
    page = client.get("/admin/users").get_data(as_text=True)
    assert "could not be sent" in page and "/set-password/" in page


def test_password_rules_and_tokens_are_stored_hashed(client):
    with pytest.raises(users.UserError):
        users.check_password("abcdefghijkl")             # no digit
    with pytest.raises(users.UserError):
        users.check_password("short1")
    uid = users.create("asha@example.com")
    tok = users.issue_token(uid, "invite")
    import sqlite3
    raw = sqlite3.connect(data.DATA_DIR / "users.db").execute("SELECT hash FROM tokens").fetchone()[0]
    assert raw != tok and tok not in raw


def test_login_network_uses_names_from_the_private_file_and_escapes_them(tmp_path, monkeypatch):
    from engine import network
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    assert network.labels() == network.GENERIC
    (tmp_path / "login_chains.txt").write_text("# comment\nAlpha Mart\n<script>x</script>\n\nBeta Stores\n", encoding="utf-8")
    names = network.labels()
    assert names == ["Alpha Mart", "<script>x</script>", "Beta Stores"]
    out = network.svg(names, "/static/logo.png")
    assert "Alpha Mart" in out and "<script>" not in out and "&lt;script&gt;" in out
    (tmp_path / "login_chains.txt").write_text("\n".join(f"Name {i}" for i in range(20)), encoding="utf-8")
    assert len(network.labels()) == network.MAX_NAMES


def test_email_failure_tells_the_admin_why(client, monkeypatch):
    make_admin(client)
    login(client, "boss@example.com", "Secret-pass-123")
    monkeypatch.setattr(mailer, "send", lambda *a: (False, "The email could not be sent: login refused"))
    t = csrf(client)
    client.post("/admin/users/new", data={"email": "asha@example.com", "csrf": t})
    page = client.get("/admin/users").get_data(as_text=True)
    assert "login refused" in page


def test_an_admin_can_create_another_admin_like_any_other_user(client):
    make_admin(client)
    login(client, "boss@example.com", "Secret-pass-123")
    t = csrf(client)
    client.post("/admin/users/new", data={"email": "second@example.com", "role": "admin", "csrf": t})
    assert users.get_by_email("second@example.com")["role"] == "admin"


def test_test_email_says_which_step_stopped_and_hides_the_password(monkeypatch):
    import smtplib
    monkeypatch.setenv("SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2-secret")
    monkeypatch.setenv("SMTP_SECURITY", "starttls")

    class Boom:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def ehlo(self):
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed hunter2-secret")
    monkeypatch.setattr(smtplib, "SMTP", Boom)
    lines = []
    assert mailer.diagnose(lines.append) is False
    text = "\n".join(lines)
    assert "say hello" in text and "hunter2-secret" not in text
