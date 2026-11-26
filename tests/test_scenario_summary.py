"""The scenario summary: saving after a sheet upload with a purpose note, the summary page and its numbers, who may see it.
Sample data and the same people as the KAM tests; never real names."""
import io
import sqlite3

import pytest
from openpyxl import load_workbook

from engine import data, scenario_sheet as ss, scenarios, simulate, users
from tests.test_kam import client_for, env, world  # noqa: F401  (fixtures)

LEVERS = {"promo_add_pp": 3, "volume_change_pct": 10, "cogs_change_pct": 2}


def person(email):
    return users.get_by_email(email)


def _summary(levers=LEVERS, scope=None, allowed=None, limited=False, name="S"):
    scn = {"name": name, "levers": levers, "scope": scope or {}, "note": "", "period": ""}
    return scenarios.summary(scn, allowed, limited)


# ---------- saving ----------
def test_a_purpose_note_and_the_period_are_saved_and_kept_on_update_and_copy(world):
    ceo = person("ceo@example.com")
    sid = scenarios.save(ceo, "Diwali", LEVERS, {}, note="  Push volume in the festive month.  ", period=data.current().key)
    scn = scenarios.get(ceo, sid)
    assert scn["note"] == "Push volume in the festive month." and scn["period"] == data.current().key
    scenarios.save(ceo, "Diwali", {"promo_add_pp": 1}, {}, scenario_id=sid)               # an update without a note keeps it
    assert scenarios.get(ceo, sid)["note"] == "Push volume in the festive month."
    scenarios.save(ceo, "Diwali", LEVERS, {}, scenario_id=sid, note="")                   # an empty note clears it
    assert scenarios.get(ceo, sid)["note"] == ""
    scenarios.save(ceo, "Diwali", LEVERS, {}, scenario_id=sid, note="Why")
    copy = scenarios.get(ceo, scenarios.duplicate(ceo, sid))
    assert copy["note"] == "Why" and copy["period"] == data.current().key


def test_a_note_that_is_too_long_is_refused_and_an_unknown_period_is_not_kept(world):
    with pytest.raises(scenarios.ScenarioError):
        scenarios.save(person("ceo@example.com"), "X", LEVERS, {}, note="x" * 1001)
    sid = scenarios.save(person("ceo@example.com"), "Y", LEVERS, {}, period="1999-01")
    assert scenarios.get(person("ceo@example.com"), sid)["period"] == ""


def test_a_database_made_before_notes_and_periods_still_works(env):
    con = sqlite3.connect(env / "users.db")
    con.executescript("CREATE TABLE scenarios (id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL DEFAULT 0, "
                      "owner_email TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, levers TEXT NOT NULL, scope TEXT NOT NULL, "
                      "created REAL NOT NULL, updated REAL NOT NULL, UNIQUE (owner_id, name));"
                      "INSERT INTO scenarios (name, levers, scope, created, updated) VALUES ('Old', '{}', '{}', 1, 1);")
    con.commit()
    con.close()
    got = scenarios.listing(None)
    assert [s["name"] for s in got] == ["Old"] and got[0]["note"] == "" and got[0]["period"] == ""


def test_a_scenario_with_thousands_of_row_changes_from_a_sheet_can_be_saved(world):
    wb = load_workbook(ss.build_template(None))
    ws = wb["Rows"]
    cols = {str(c.value): c.column for c in ws[1]}
    vol = cols["Scenario: Volume ('000 L)"]
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=vol).value = ws.cell(row=r, column=vol - 1).value * 1.1
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    out = ss.read_upload(buf)
    assert out["report"]["changed_rows"] == len(data.ROWS)
    sid = scenarios.save(person("ceo@example.com"), "From sheet", {**out["levers"], "adjust": out["adjust"]}, {})
    back = scenarios.get(person("ceo@example.com"), sid)
    assert len(back["levers"]["adjust"]["row"]) == len(data.ROWS)


# ---------- the numbers ----------
def test_the_summary_holds_the_p_and_l_baseline_against_scenario_and_it_matches_the_engine():
    d = _summary()
    want = simulate.headline(LEVERS, {})
    by = {l["key"]: l for l in d["lines"]}
    assert by["net"]["base"] == pytest.approx(want["base"]["net"]) and by["net"]["sim"] == pytest.approx(want["sim"]["net"])
    assert by["cm"]["sim"] == pytest.approx(want["sim"]["cm"]) and by["nm_pct"]["sim"] == pytest.approx(want["sim"]["nm_pct"])
    days = data.days_per_month()
    assert by["vol_l"]["sim"] == pytest.approx((want["sim"]["litres"] + want["sim"]["kg"]) / days)
    assert not any(k in by for k in ("units", "litres", "kg"))                         # litres only, no packs or kg
    assert by["cogs"]["cost"] and by["promo"]["cost"] and not by["net"]["cost"]
    assert d["rows_in_scope"] == d["rows_total"] == len(data.ROWS)


def test_it_says_what_was_changed_globally_and_row_by_row():
    chain = str(data.ROWS[0]["chain"])
    d = _summary({"cogs_change_pct": 2, "adjust": {"chain": {chain: {"promo_add_pp": 2, "volume_change_pct": 5}}}})
    assert [l["value"] for l in d["levers"]] == [2.0] and "COGS" in d["levers"][0]["label"]
    in_chain = sum(1 for r in data.ROWS if str(r["chain"]) == chain)
    assert d["rows_changed"] == in_chain
    by = {r["label"]: r for r in d["row_levers"]}
    assert len(by) == 2 and all(r["rows"] == in_chain for r in by.values())
    assert {r["low"] for r in by.values()} == {2.0, 5.0} and {r["high"] for r in by.values()} == {2.0, 5.0}


def test_the_bridge_reconciles_baseline_to_scenario_and_the_state_table_adds_up():
    d = _summary()
    steps = d["bridge"]
    assert steps[0]["value"] + sum(s["value"] for s in steps[1:-1]) == pytest.approx(steps[-1]["value"])
    by = {l["key"]: l for l in d["lines"]}
    assert steps[-1]["value"] == pytest.approx(by["cm"]["sim"]) and steps[0]["value"] == pytest.approx(by["cm"]["base"])
    state = next(w for w in d["where"] if w["dimension"] == "State")
    for w in d["where"]:                                                      # every group is listed and each table adds to the headline
        assert w["totals"]["d_cm"] == pytest.approx(by["cm"]["sim"] - by["cm"]["base"])
        assert w["totals"]["d_net"] == pytest.approx(sum(i["d_net"] for i in w["items"]))
        assert w["totals"]["rows_affected"] == d["rows_in_scope"]          # LEVERS move every row


def test_the_rows_that_move_most_show_the_numbers_they_were_given():
    i = 0
    row = data.ROWS[i]
    levers = {"adjust": {"row": {row["rowkey"]: {"promo_add_pp": 2, "volume_change_pct": 20}}}}
    d = _summary(levers)
    assert len(d["top_rows"]) == 1
    top = d["top_rows"][0]
    steps = {c["label"]: c for c in top["changes"]}
    assert {"Volume ('000 L)", "Promo per litre (Rs)"} <= set(steps)
    v = steps["Volume ('000 L)"]
    assert v["scenario"] == pytest.approx(v["base"] * 1.2)
    assert top["d_cm"] == pytest.approx(simulate.scenario_metrics(simulate.parse_levers(levers), simulate.parse_scope({}), adjust=levers["adjust"])[i]["cm"] - simulate.base_metrics()[i]["cm"])


def test_an_unchanged_scenario_has_nothing_to_report():
    d = _summary({})
    assert d["levers"] == [] and d["row_levers"] == [] and d["rows_changed"] == 0 and d["top_rows"] == []
    assert all(w["items"] == [] for w in d["where"])
    assert all(l["sim"] == pytest.approx(l["base"]) for l in d["lines"])


def test_the_scope_is_in_words_and_limits_the_rows():
    chain = str(data.ROWS[0]["chain"])
    d = _summary(LEVERS, {"chain": [chain]})
    assert d["scope"] == [{"dimension": "Chain", "names": [data.LABEL["chain"].get(chain, chain)]}]
    assert d["rows_in_scope"] == sum(1 for r in data.ROWS if str(r["chain"]) == chain) < d["rows_total"]


# ---------- who sees it ----------
def test_a_kam_summary_stops_at_contribution_and_covers_only_their_rows(world):
    from engine import access
    asha = person("asha@example.com")
    allowed, limited = access.allowed_indices(asha), access.limited(asha)
    d = _summary(LEVERS, allowed=allowed, limited=limited)
    keys = {l["key"] for l in d["lines"]}
    assert "cm" in keys and not keys & simulate.BELOW_CONTRIBUTION and d["limited"]
    assert d["rows_in_scope"] == len(allowed) < len(data.ROWS)


def test_the_summary_page_and_data_follow_the_scenarios_visibility_rules(world):
    sid = scenarios.save(person("asha@example.com"), "Asha plan", LEVERS, {}, note="Because", period=data.current().key)
    asha, ravi, ceo = (client_for("asha@example.com", "Own-pass-12345"), client_for("ravi@example.com", "Own-pass-12345"),
                       client_for("ceo@example.com", "Ceo-pass-12345"))
    for c in (asha, ceo):
        page = c.get(f"/scenarios/{sid}")
        assert page.status_code == 200 and b"Scenario summary" in page.data
        body = c.get(f"/api/scenarios/{sid}/summary").get_json()
        assert body["scenario"]["name"] == "Asha plan" and body["scenario"]["note"] == "Because"
        assert body["period"] and {"lines", "levers", "bridge", "where", "top_rows"} <= set(body)
    assert ravi.get(f"/scenarios/{sid}").status_code == 404
    assert ravi.get(f"/api/scenarios/{sid}/summary").status_code == 404
    kam = asha.get(f"/api/scenarios/{sid}/summary").get_json()
    assert kam["limited"] and "nm" not in {l["key"] for l in kam["lines"]}
    assert "nm" in {l["key"] for l in ceo.get(f"/api/scenarios/{sid}/summary").get_json()["lines"]}       # leadership sees below contribution


def test_saving_from_the_route_keeps_the_note_the_period_and_never_overwrites_a_same_named_scenario(world):
    c = client_for("ceo@example.com", "Ceo-pass-12345")
    first = c.post("/api/scenarios", json={"name": "Plan", "levers": LEVERS, "scope": {}, "note": "Why", "copy": True}).get_json()["id"]
    second = c.post("/api/scenarios", json={"name": "Plan", "levers": {"promo_add_pp": 1}, "scope": {}, "copy": True}).get_json()["id"]
    assert first != second
    got = {s["id"]: s for s in c.get("/api/scenarios?results=0").get_json()["scenarios"]}
    assert got[first]["name"] == "Plan" and got[second]["name"] == "Plan (2)"
    assert got[first]["note"] == "Why" and got[first]["period"] == data.current().key


def test_a_scenario_saved_on_a_period_that_is_gone_is_summarised_on_the_one_in_use(world):
    sid = scenarios.save(person("ceo@example.com"), "Old period", LEVERS, {})
    with scenarios._db() as con:
        con.execute("UPDATE scenarios SET period='1999-01' WHERE id=?", (sid,))
    r = client_for("ceo@example.com", "Ceo-pass-12345").get(f"/api/scenarios/{sid}/summary")
    assert r.status_code == 200 and r.get_json()["scenario"]["differs"]


def test_the_changes_workbook_lists_every_changed_row_and_matches_the_summary(world):
    ceo = client_for("ceo@example.com", "Ceo-pass-12345")
    sid = ceo.post("/api/scenarios", json={"name": "All", "levers": {"promo_add_pp": 2}, "scope": {}, "copy": True}).get_json()["id"]
    got = ceo.post(f"/api/scenarios/{sid}/changes")
    assert got.status_code == 200
    ws = load_workbook(io.BytesIO(got.data))["Changed rows"]
    summ = ceo.get(f"/api/scenarios/{sid}/summary").get_json()
    assert ws.max_row - 1 == summ["rows_in_scope"] > 0          # a global lever moves every row
    assert sum(r[-1].value for r in ws.iter_rows(min_row=2)) == pytest.approx(summ["where"][0]["totals"]["d_cm"] / 1e5, rel=1e-6)
    assert client_for("ravi@example.com", "Own-pass-12345").post(f"/api/scenarios/{sid}/changes").status_code == 404
