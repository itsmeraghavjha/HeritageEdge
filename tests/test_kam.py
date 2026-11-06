"""KAM access: who sees which rows, nothing below contribution for a KAM, the mapping upload and the forced password change.
Uses the synthetic sample data (chains "Sample Chain A" to "F"); never real names."""
import io
import json

import pytest
from openpyxl import Workbook

import app as app_module
from engine import access, data, mailer, simulate, users

LIMITED = simulate.BELOW_CONTRIBUTION


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mailer, "send", lambda *a: (True, ""))
    monkeypatch.setattr(mailer, "configured", lambda: True)
    return tmp_path


def sheet(rows, header=("StateCluster Name", "State", "Cluster Name", "Employee ID", "Employee Name", "Email")):
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


KAM_ROWS = [
    ("TelanganaSample Chain A", "Telangana", "Sample Chain A", 101, "Asha Rao", "asha@example.com"),
    ("Andhra PradeshSample Chain B", "Andhra Pradesh", "SAMPLE-CHAIN B", 101, "Asha Rao", "asha@example.com"),
    ("KarnatakaSample Chain C", "Karnataka", "Sample Chain C", 102, "Ravi Kumar", "ravi@example.com"),
    ("TelanganaNo Such Chain", "Telangana", "No Such Chain", 103, "Meera Nair", "meera@example.com"),
]


def login(c, email, pw):
    c.get("/login")
    with c.session_transaction() as s:
        s["csrf"] = "t"
    return c.post("/login", data={"email": email, "password": pw, "csrf": "t"})


def post_json(c, path, body):
    return c.post(path, data=json.dumps(body), content_type="application/json")


@pytest.fixture
def world(env):
    """An admin, a leadership user and three KAMs from a sheet, all with their own passwords."""
    admin = users.create("boss@example.com", "Boss", "admin")
    users.set_password_with_token(users.issue_token(admin, "invite"), "Boss-pass-12345")
    lead = users.create("ceo@example.com", "CEO", "leadership")
    users.set_password_with_token(users.issue_token(lead, "invite"), "Ceo-pass-12345")
    report = access.import_mapping(sheet(KAM_ROWS), "Start-pass-123")
    for email in ("asha@example.com", "ravi@example.com", "meera@example.com"):
        u = users.get_by_email(email)
        users.change_password(users.login(email, "Start-pass-123")["id"], "Start-pass-123", "Own-pass-12345")
    return report


def client_for(email, pw):
    c = app_module.app.test_client()
    assert login(c, email, pw).status_code == 302
    return c


def chains_states(res):
    """Every chain and state label appearing in a /api/pnl answer."""
    seen = set()

    def walk(nodes):
        for n in nodes:
            seen.add(n["label"])
            walk(n.get("children", []))
    walk(res["tree"])
    return seen


# ---------- the sheet ----------
def test_import_creates_kams_with_a_starting_password_that_must_change(env):
    report = access.import_mapping(sheet(KAM_ROWS), "Start-pass-123")
    assert "3 new KAMs created" in report
    assert "No Such Chain" in report                       # reported as unmatched
    asha = users.get_by_email("asha@example.com")
    assert asha["role"] == "kam" and asha["must_change"] == 1 and asha["employee_id"] == "101"
    assert len(users.scope_rows(asha["id"])) == 2          # two rows merged into one KAM
    assert users.login("asha@example.com", "Start-pass-123")["id"] == asha["id"]


def test_reupload_updates_mapping_and_keeps_passwords(env):
    access.import_mapping(sheet(KAM_ROWS), "Start-pass-123")
    asha = users.get_by_email("asha@example.com")
    users.change_password(asha["id"], "Start-pass-123", "Own-pass-12345")
    access.import_mapping(sheet([KAM_ROWS[0]]), "Another-pass-1")
    after = users.get_by_email("asha@example.com")
    assert [s["cluster"] for s in users.scope_rows(after["id"])] == ["Sample Chain A"]
    assert after["must_change"] == 0
    assert users.login("asha@example.com", "Own-pass-12345")


def test_bad_rows_are_skipped_and_a_missing_header_is_refused(env):
    rows = [("x", "Telangana", "Sample Chain A", 1, "A", "not-an-email"), KAM_ROWS[0]]
    report = access.import_mapping(sheet(rows), "Start-pass-123")
    assert "not a valid email" in report and users.get_by_email("asha@example.com")
    with pytest.raises(users.UserError):
        access.import_mapping(sheet([("a", "b")], header=("One", "Two")), "Start-pass-123")
    with pytest.raises(users.UserError):
        access.import_mapping(sheet(KAM_ROWS), "short")


def test_the_admin_and_leadership_are_not_touched_by_an_upload(env):
    lead = users.create("asha@example.com", "Asha", "leadership")
    report = access.import_mapping(sheet(KAM_ROWS), "Start-pass-123")
    assert users.get(lead)["role"] == "leadership" and "Left alone" in report


# ---------- who sees which rows ----------
def test_a_kam_sees_only_mapped_state_and_chain_names_ignoring_case_and_punctuation(world):
    asha = users.get_by_email("asha@example.com")
    idx = access.allowed_indices(asha)
    pairs = {(data.ROWS[i]["state"], data.ROWS[i]["chain"]) for i in idx}
    assert pairs == {("Telangana", "Sample Chain A"), ("Andhra Pradesh", "Sample Chain B")}


def test_a_kam_with_an_unmatched_cluster_sees_nothing_until_it_has_an_alias(world):
    meera = users.get_by_email("meera@example.com")
    assert access.allowed_indices(meera) == []
    assert access.unmatched_clusters() == ["No Such Chain"]
    users.set_alias(access.norm("No Such Chain"), access.norm("Sample Chain D"))
    assert access.unmatched_clusters() == []
    assert {data.ROWS[i]["chain"] for i in access.allowed_indices(meera)} == {"Sample Chain D"}


def test_admin_and_leadership_see_every_row(world):
    for email in ("boss@example.com", "ceo@example.com"):
        assert access.allowed_indices(users.get_by_email(email)) is None


def test_meta_lists_only_the_kams_own_chains_states_and_skus(world):
    c = client_for("ravi@example.com", "Own-pass-12345")
    meta = c.get("/api/meta").get_json()
    dims = {d["id"]: {o["name"] for o in d["options"]} for d in meta["dims"]}
    assert dims["chain"] == {"Sample Chain C"} and dims["state"] == {"Karnataka"}
    assert all(row[data.DIM_IDS.index("chain")] == "Sample Chain C" for row in meta["combos"])
    full = client_for("ceo@example.com", "Ceo-pass-12345").get("/api/meta").get_json()
    assert len({o["name"] for d in full["dims"] if d["id"] == "chain" for o in d["options"]}) > 1


def test_kam_numbers_match_the_same_selection_made_by_leadership(world):
    kam = client_for("ravi@example.com", "Own-pass-12345")
    lead = client_for("ceo@example.com", "Ceo-pass-12345")
    mine = post_json(kam, "/api/simulate", {"levers": {"promo_add_pp": 2}}).get_json()
    sel = {"scope": {"chain": ["Sample Chain C"], "state": ["Karnataka"]}, "levers": {"promo_add_pp": 2}}
    theirs = post_json(lead, "/api/simulate", sel).get_json()
    assert mine["sim"]["cm"] == pytest.approx(theirs["sim"]["cm"])
    assert mine["base"]["net"] == pytest.approx(theirs["base"]["net"])


def test_whole_business_totals_do_not_leak_through_whole_or_rows_total(world):
    kam = client_for("ravi@example.com", "Own-pass-12345")
    res = post_json(kam, "/api/simulate", {}).get_json()
    assert res["rows_total"] == res["rows_in_scope"] < len(data.ROWS)
    assert res["whole"]["net"] == pytest.approx(res["base"]["net"])


def test_a_kam_cannot_widen_the_view_with_a_scope_or_filter(world):
    kam = client_for("ravi@example.com", "Own-pass-12345")
    body = {"scope": {"chain": ["Sample Chain A", "Sample Chain B"], "state": ["Telangana"]},
            "filters": {"chain": ["Sample Chain A"]}, "dims": ["chain", "state"]}
    res = post_json(kam, "/api/pnl", body).get_json()
    assert res["rows_shown"] == 0 and res["tree"] == []
    res = post_json(kam, "/api/pnl", {"dims": ["chain", "state", "sku"]}).get_json()
    assert chains_states(res) >= {"Sample Chain C", "Karnataka"}
    assert not ({"Sample Chain A", "Sample Chain B", "Telangana"} & chains_states(res))


# ---------- nothing below contribution for a KAM ----------
def keys_in(obj, found=None):
    found = set() if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            keys_in(v, found)
    elif isinstance(obj, list):
        for v in obj:
            keys_in(v, found)
    return found


def test_a_kam_gets_no_lines_below_contribution_anywhere(world):
    kam = client_for("ravi@example.com", "Own-pass-12345")
    sim = post_json(kam, "/api/simulate", {"levers": {"promo_add_pp": 2, "volume_change_pct": 10}}).get_json()
    pnl = post_json(kam, "/api/pnl", {"dims": ["chain", "state", "sku"]}).get_json()
    for res in (sim, pnl):
        assert not (keys_in(res) & LIMITED)
        assert "cm" in keys_in(res) and "cm_pct" in keys_in(res)
    lead = client_for("ceo@example.com", "Ceo-pass-12345")
    full = post_json(lead, "/api/simulate", {}).get_json()
    assert LIMITED <= keys_in(full)


def test_kam_excel_export_has_no_lines_below_contribution_and_only_their_rows(world):
    from openpyxl import load_workbook
    kam = client_for("ravi@example.com", "Own-pass-12345")
    res = post_json(kam, "/api/export", {"dims": ["chain", "state", "sku"]})
    wb = load_workbook(io.BytesIO(res.data))
    text = " ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None)
    for word in ("Net margin", "NM %", "P&A", "Interest", "Marketing"):
        assert word not in text
    assert "Contribution" in text
    assert "Sample Chain A" not in text and "Telangana" not in text


# ---------- pages and the forced password change ----------
def test_only_the_admin_opens_users_and_data(world):
    for email, pw in (("ravi@example.com", "Own-pass-12345"), ("ceo@example.com", "Ceo-pass-12345")):
        c = client_for(email, pw)
        assert c.get("/admin/users").status_code == 403
        assert c.get("/data").status_code == 403
        assert c.get("/api/data/status").status_code == 403
        assert c.post("/api/data/build", json={}).status_code == 403
        assert c.post("/admin/users/upload", data={}).status_code in (400, 403)
    admin = client_for("boss@example.com", "Boss-pass-12345")
    assert admin.get("/admin/users").status_code == 200 and admin.get("/data").status_code == 200


def test_first_login_with_the_starting_password_forces_a_change(env):
    admin = users.create("boss@example.com", "Boss", "admin")
    users.set_password_with_token(users.issue_token(admin, "invite"), "Boss-pass-12345")
    access.import_mapping(sheet(KAM_ROWS), "Start-pass-123")
    c = app_module.app.test_client()
    assert login(c, "asha@example.com", "Start-pass-123").status_code == 302
    assert "/change-password" in c.get("/").headers["Location"]
    assert c.post("/api/simulate", json={}).status_code == 403
    assert c.get("/change-password").status_code == 200
    with c.session_transaction() as s:
        s["csrf"] = "t"
    bad = c.post("/change-password", data={"current": "wrong-pass-1", "password": "New-pass-12345",
                                           "again": "New-pass-12345", "csrf": "t"})
    assert "not right" in bad.get_data(as_text=True)
    ok = c.post("/change-password", data={"current": "Start-pass-123", "password": "New-pass-12345",
                                          "again": "New-pass-12345", "csrf": "t"})
    assert ok.status_code == 302
    assert c.get("/").status_code == 200


def test_admin_uploads_the_sheet_and_edits_one_kams_mapping(world):
    admin = client_for("boss@example.com", "Boss-pass-12345")
    with admin.session_transaction() as s:
        s["csrf"] = "t"
    res = admin.post("/admin/users/upload", data={
        "csrf": "t", "starting_password": "Start-pass-456",
        "file": (sheet([("x", "Karnataka", "Sample Chain E", 104, "New Person", "new@example.com")]), "kam.xlsx")},
        content_type="multipart/form-data")
    assert res.status_code == 302 and users.get_by_email("new@example.com")
    uid = users.get_by_email("new@example.com")["id"]
    admin.post(f"/admin/users/{uid}/scope/add", data={"csrf": "t", "state": "Telangana", "cluster": "Sample Chain F"})
    assert len(users.scope_rows(uid)) == 2
    page = admin.get(f"/admin/users/{uid}").get_data(as_text=True)
    assert "Sample Chain F" in page
    sid = users.scope_rows(uid)[0]["id"]
    admin.post(f"/admin/users/{uid}/scope/{sid}/remove", data={"csrf": "t"})
    assert len(users.scope_rows(uid)) == 1
    admin.post(f"/admin/users/{uid}/details", data={"csrf": "t", "name": "New P", "employee_id": "9", "role": "leadership"})
    assert users.get(uid)["role"] == "leadership" and access.allowed_indices(users.get(uid)) is None


def test_old_user_role_becomes_leadership(env):
    import sqlite3
    users.has_users()
    con = sqlite3.connect(env / "users.db")
    con.execute("INSERT INTO users (email, name, role, created) VALUES ('old@example.com', '', 'user', 0)")
    con.commit()
    con.close()
    assert users.get_by_email("old@example.com")["role"] == "leadership"


def test_a_kam_with_no_accounts_gets_an_explanation_not_a_page_of_zeros(world):
    """Meera's only cluster matches no chain, so she sees an explanation on both screens; others see the real screens."""
    c = client_for("meera@example.com", "Own-pass-12345")
    for path in ("/", "/pnl"):
        html = c.get(path).get_data(as_text=True)
        assert "No accounts are assigned to you yet" in html
        assert "scenarioPanel" not in html and "pnlTable" not in html
    ok = client_for("asha@example.com", "Own-pass-12345").get("/").get_data(as_text=True)
    assert "No accounts are assigned to you yet" not in ok
    assert "All my accounts" in ok
