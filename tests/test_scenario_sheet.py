"""The scenario sheet: download the levers and row adjustments as Excel, change them there, upload them back.
The sheet only fills the inputs the engine already takes, so the key tests are that it round-trips and that an uploaded
sheet gives exactly the numbers the same changes give when typed on the screen. Synthetic sample data only."""
import io
import json

import pytest
from openpyxl import Workbook, load_workbook

from engine import access, data, scenario_sheet as ss
from engine import simulate as sim
from tests.test_kam import client_for, env, world  # noqa: F401  (fixtures)


def _filled(levers=None, edits=()):
    """A template for `levers`, with (sheet, code, lever, value) cells set, as an open workbook."""
    wb = load_workbook(ss.build_template(levers))
    for sheet, code, lever, value in edits:
        ws = wb[sheet]
        col = 3 + sim.ADJUST_LEVERS.index(lever)
        last = 3 + len(sim.ADJUST_LEVERS)
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(row=r, column=last).value) == str(code):
                ws.cell(row=r, column=col).value = value
                break
        else:
            raise AssertionError(f"{code} not on {sheet}")
    return wb


def _upload(wb, allowed=None):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return ss.read_upload(buf, allowed)


def _chain():
    return str(data.ROWS[0]["chain"])


def _sku():
    return str(data.ROWS[0]["sku"])


def test_a_blank_sheet_means_no_change():
    out = _upload(_filled())
    assert out["adjust"] == {} and all(v == 0 for v in out["levers"].values())
    assert out["report"]["changed_rows"] == 0 and out["report"]["levers_set"] == 0


def test_the_sheet_gives_the_same_numbers_as_typing_the_changes_on_screen():
    chain, sku = _chain(), _sku()
    out = _upload(_filled(edits=[("Chain", chain, "promo_add_pp", 3), ("Chain", chain, "volume_change_pct", 10),
                                 ("SKU", sku, "price_change_pct", 2)]))
    assert out["adjust"] == {"chain": {chain: {"promo_add_pp": 3.0, "volume_change_pct": 10.0}},
                             "sku": {sku: {"price_change_pct": 2.0}}}
    typed = {"adjust": {"chain": {chain: {"promo_add_pp": 3, "volume_change_pct": 10}}, "sku": {sku: {"price_change_pct": 2}}}}
    from_sheet = {**out["levers"], "adjust": out["adjust"]}
    assert sim.simulate(from_sheet, {}) == sim.simulate(typed, {})
    assert sim.pnl(from_sheet, {}, ["chain"]) == sim.pnl(typed, {}, ["chain"])


def test_the_levers_sheet_sets_the_seven_levers():
    wb = _filled()
    ws = wb["Levers"]
    values = {"price_change_pct": 2, "promo_add_pp": 1.5, "volume_change_pct": -5, "cogs_change_pct": 4}
    for r in range(2, ws.max_row + 1):
        key = ws.cell(row=r, column=5).value
        if key in values:
            ws.cell(row=r, column=2).value = values[key]
    out = _upload(wb)
    assert {k: v for k, v in out["levers"].items() if v} == values and out["report"]["levers_set"] == 4
    assert set(out["levers"]) == set(sim.LIMITS)


def test_what_is_set_now_comes_back_filled_in_and_round_trips():
    chain = _chain()
    now = {"price_change_pct": 1, "freight_change_pct": 5, "adjust": {"chain": {chain: {"rebate_add_pp": 2}}}}
    out = _upload(load_workbook(ss.build_template(now)))
    assert out["levers"]["price_change_pct"] == 1 and out["levers"]["freight_change_pct"] == 5
    assert out["adjust"] == {"chain": {chain: {"rebate_add_pp": 2.0}}}


def test_values_outside_the_allowed_range_are_brought_back_and_reported():
    chain = _chain()
    out = _upload(_filled(edits=[("Chain", chain, "promo_add_pp", 50)]))
    assert out["adjust"]["chain"][chain]["promo_add_pp"] == sim.LIMITS["promo_add_pp"][1]
    assert any("50" in c and "20" in c for c in out["report"]["clamped"])


def test_a_cell_that_is_not_a_number_stops_the_upload_and_names_the_cell():
    chain = _chain()
    with pytest.raises(ss.SheetError) as exc:
        _upload(_filled(edits=[("Chain", chain, "promo_add_pp", "three")]))
    assert "Chain sheet" in str(exc.value) and "three" in str(exc.value)


def test_percent_signs_and_numbers_stored_as_text_are_accepted():
    chain = _chain()
    out = _upload(_filled(edits=[("Chain", chain, "volume_change_pct", "10%"), ("Chain", chain, "promo_add_pp", " 2 ")]))
    assert out["adjust"]["chain"][chain] == {"volume_change_pct": 10.0, "promo_add_pp": 2.0}


def test_a_code_that_is_not_in_the_data_is_ignored_and_reported():
    wb = _filled()
    ws = wb["SKU"]
    ws.append(["Made up", 0, None, None, None, None, 5, "99999999"])
    ws.cell(row=ws.max_row, column=7).value = 5
    out = _upload(wb)
    assert out["adjust"] == {} and any("SKU sheet" in i for i in out["report"]["ignored"])


def test_a_sku_code_that_excel_turned_into_a_number_still_matches():
    sku = _sku()
    wb = _filled(edits=[("SKU", sku, "price_change_pct", 1)])
    ws = wb["SKU"]
    last = 3 + len(sim.ADJUST_LEVERS)
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(row=r, column=last).value) == sku and sku.isdigit():
            ws.cell(row=r, column=last).value = int(sku)
    assert _upload(wb)["adjust"] == {"sku": {sku: {"price_change_pct": 1.0}}}


def test_files_that_are_not_a_scenario_sheet_are_refused_in_plain_words():
    with pytest.raises(ss.SheetError) as exc:
        ss.read_upload(io.BytesIO(b"not excel"))
    assert "Excel" in str(exc.value)
    wb = Workbook()
    wb.active.title = "Something else"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with pytest.raises(ss.SheetError) as exc:
        ss.read_upload(buf)
    assert "not a scenario sheet" in str(exc.value)


def test_the_sheets_list_every_chain_largest_first_with_its_baseline_net_revenue():
    ws = load_workbook(ss.build_template(None))["Chain"]
    nets = [ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)]
    assert nets == sorted(nets, reverse=True) and len(nets) == len({r["chain"] for r in data.ROWS})
    total = sum(m["net"] for m in sim.base_metrics()) / 1e5
    assert sum(nets) == pytest.approx(total)


def test_a_kam_gets_only_their_own_rows_and_cannot_set_anyone_elses(world):
    allowed = access.allowed_indices(access.users.get_by_email("asha@example.com")) if hasattr(access, "users") else None
    if allowed is None:
        from engine import users
        allowed = access.allowed_indices(users.get_by_email("asha@example.com"))
    mine = {str(data.ROWS[i]["chain"]) for i in allowed}
    ws = load_workbook(ss.build_template(None, allowed))["Chain"]
    listed = {str(ws.cell(row=r, column=8).value) for r in range(2, ws.max_row + 1)}
    assert listed == mine and len(mine) < len({str(r["chain"]) for r in data.ROWS})
    other = next(str(r["chain"]) for r in data.ROWS if str(r["chain"]) not in mine)
    wb = _filled(edits=[("Chain", other, "promo_add_pp", 3)])               # a full sheet edited to reach another chain
    out = _upload(wb, allowed)
    assert out["adjust"] == {} and out["report"]["ignored"]


def test_the_routes_download_and_upload_and_log_only_counts(world):
    c = client_for("ceo@example.com", "Ceo-pass-12345")
    r = c.post("/api/scenario-sheet/download", data=json.dumps({"levers": {}}), content_type="application/json")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    chain = _chain()
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Chain"]
    last = 3 + len(sim.ADJUST_LEVERS)
    for row in range(2, ws.max_row + 1):
        if str(ws.cell(row=row, column=last).value) == chain:
            ws.cell(row=row, column=4).value = 2.5
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    up = c.post("/api/scenario-sheet/upload", data={"file": (buf, "mine.xlsx")}, content_type="multipart/form-data")
    body = up.get_json()
    assert up.status_code == 200 and body["adjust"] == {"chain": {chain: {"promo_add_pp": 2.5}}}
    from engine import activity
    events, _ = activity.recent(50)
    mine = [e for e in events if e["action"] in ("sheet_download", "sheet_upload")]
    assert {e["action"] for e in mine} == {"sheet_download", "sheet_upload"}
    seen = " ".join(f"{e['action']} {e['detail']}" for e in mine)
    assert "Sample" not in seen and "2.5" not in seen              # events and counts, never names or values
    bad = c.post("/api/scenario-sheet/upload", data={"file": (io.BytesIO(b"nope"), "x.xlsx")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "Excel" in bad.get_json()["error"]
    assert c.post("/api/scenario-sheet/upload", data={}, content_type="multipart/form-data").status_code == 400


def test_the_sheet_routes_need_a_login(world):
    import app
    anon = app.app.test_client()
    assert anon.post("/api/scenario-sheet/download", data="{}", content_type="application/json").status_code in (401, 302, 403)
    assert anon.post("/api/scenario-sheet/upload", data={}, content_type="multipart/form-data").status_code in (401, 302, 403)
