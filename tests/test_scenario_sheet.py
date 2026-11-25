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


N_DIMS = len(data.DIM_DEFS)
FIRST = N_DIMS + 2                                  # first lever column on the Rows sheet
KEY = FIRST + len(sim.ADJUST_LEVERS)                # the key column


def _filled(levers=None, edits=(), allowed=None):
    """A template for `levers`, with (row key, lever, value) cells set on the Rows sheet, as an open workbook."""
    wb = load_workbook(ss.build_template(levers, allowed))
    ws = wb["Rows"]
    where = {str(ws.cell(row=r, column=KEY).value): r for r in range(2, ws.max_row + 1)}
    for key, lever, value in edits:
        ws.cell(row=where[key], column=FIRST + sim.ADJUST_LEVERS.index(lever)).value = value
    return wb


def _upload(wb, allowed=None):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return ss.read_upload(buf, allowed)


def _key(i=0):
    return data.ROWS[i]["rowkey"]


def test_a_blank_sheet_means_no_change():
    out = _upload(_filled())
    assert out["adjust"] == {} and all(v == 0 for v in out["levers"].values())
    assert out["report"]["changed_rows"] == 0 and out["report"]["levers_set"] == 0


def test_one_line_is_one_row_of_the_baseline_largest_first():
    ws = load_workbook(ss.build_template(None))["Rows"]
    assert ws.max_row - 1 == len(data.ROWS)
    nets = [ws.cell(row=r, column=N_DIMS + 1).value for r in range(2, ws.max_row + 1)]
    assert nets == sorted(nets, reverse=True)
    assert sum(nets) == pytest.approx(sum(m["net"] for m in sim.base_metrics()) / 1e5)
    assert len({ws.cell(row=r, column=KEY).value for r in range(2, ws.max_row + 1)}) == len(data.ROWS)   # keys are unique
    assert ws.auto_filter.ref and ws.column_dimensions[ws.cell(row=1, column=KEY).column_letter].hidden


def test_a_change_on_one_sku_in_one_state_for_one_chain_moves_only_that_row():
    i = 0
    row = data.ROWS[i]
    out = _upload(_filled(edits=[(_key(i), "promo_add_pp", 3), (_key(i), "volume_change_pct", 10)]))
    assert out["adjust"] == {"row": {_key(i): {"promo_add_pp": 3.0, "volume_change_pct": 10.0}}}
    sims = sim.scenario_metrics(sim.parse_levers(out["levers"]), sim.parse_scope({}), adjust=out["adjust"])
    base = sim.base_metrics()
    moved = [j for j, (a, b) in enumerate(zip(base, sims)) if a != b]
    assert moved == [i]
    assert sims[i]["units"] == pytest.approx(base[i]["units"] * 1.10)
    # and a second row with the same chain, state or SKU is untouched
    same = [j for j, r in enumerate(data.ROWS) if j != i and (r["chain"] == row["chain"] or r["sku"] == row["sku"] or r["state"] == row["state"])]
    assert same and all(sims[j] == base[j] for j in same)


def test_the_sheet_gives_the_same_numbers_as_the_same_change_typed_on_screen():
    chain = str(data.ROWS[0]["chain"])
    keys = [r["rowkey"] for r in data.ROWS if str(r["chain"]) == chain]
    out = _upload(_filled(edits=[(k, "promo_add_pp", 3) for k in keys] + [(k, "volume_change_pct", 10) for k in keys]))
    typed = {"adjust": {"chain": {chain: {"promo_add_pp": 3, "volume_change_pct": 10}}}}
    from_sheet = {**out["levers"], "adjust": out["adjust"]}
    assert sim.simulate(from_sheet, {}) == sim.simulate(typed, {})


def test_what_is_set_on_the_screen_comes_back_on_every_row_it_reaches_and_gives_the_same_numbers():
    chain, sku = str(data.ROWS[0]["chain"]), str(data.ROWS[0]["sku"])
    now = {"price_change_pct": 1, "freight_change_pct": 5,
           "adjust": {"chain": {chain: {"rebate_add_pp": 2}}, "sku": {sku: {"promo_add_pp": 1, "rebate_add_pp": 1}}}}
    out = _upload(load_workbook(ss.build_template(now)))
    assert out["levers"]["price_change_pct"] == 1 and out["levers"]["freight_change_pct"] == 5
    assert set(out["adjust"]) == {"row"}
    both = next(r for r in data.ROWS if str(r["chain"]) == chain and str(r["sku"]) == sku)
    assert out["adjust"]["row"][both["rowkey"]] == {"rebate_add_pp": 3.0, "promo_add_pp": 1.0}     # a chain and a SKU add up
    assert sim.simulate({**out["levers"], "adjust": out["adjust"]}, {}) == sim.simulate(now, {})


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


def test_values_outside_the_allowed_range_are_brought_back_and_reported():
    out = _upload(_filled(edits=[(_key(), "promo_add_pp", 50)]))
    assert out["adjust"]["row"][_key()]["promo_add_pp"] == sim.LIMITS["promo_add_pp"][1]
    assert any("50" in c and "20" in c for c in out["report"]["clamped"])


def test_a_cell_that_is_not_a_number_stops_the_upload_and_names_the_cell():
    with pytest.raises(ss.SheetError) as exc:
        _upload(_filled(edits=[(_key(), "promo_add_pp", "three")]))
    assert "Rows sheet" in str(exc.value) and "three" in str(exc.value)


def test_percent_signs_and_numbers_stored_as_text_are_accepted():
    out = _upload(_filled(edits=[(_key(), "volume_change_pct", "10%"), (_key(), "promo_add_pp", " 2 ")]))
    assert out["adjust"]["row"][_key()] == {"volume_change_pct": 10.0, "promo_add_pp": 2.0}


def test_a_line_that_is_not_a_row_of_the_baseline_is_ignored_and_reported():
    wb = _filled()
    ws = wb["Rows"]
    ws.append(["x"] * N_DIMS + [0, 5, None, None, None, None, "no|such|row"])
    out = _upload(wb)
    assert out["adjust"] == {} and any("Rows sheet" in i for i in out["report"]["ignored"])


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


def test_a_kam_gets_only_their_own_rows_and_cannot_set_anyone_elses(world):
    from engine import users
    allowed = access.allowed_indices(users.get_by_email("asha@example.com"))
    mine = {data.ROWS[i]["rowkey"] for i in allowed}
    ws = load_workbook(ss.build_template(None, allowed))["Rows"]
    listed = {ws.cell(row=r, column=KEY).value for r in range(2, ws.max_row + 1)}
    assert listed == mine and 0 < len(mine) < len(data.ROWS)
    other = next(r["rowkey"] for r in data.ROWS if r["rowkey"] not in mine)
    out = _upload(_filled(edits=[(other, "promo_add_pp", 3)]), allowed)         # a full sheet edited to reach another row
    assert out["adjust"] == {} and out["report"]["ignored"]


def test_the_routes_download_and_upload_and_log_only_counts(world):
    c = client_for("ceo@example.com", "Ceo-pass-12345")
    r = c.post("/api/scenario-sheet/download", data=json.dumps({"levers": {}}), content_type="application/json")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    wb = load_workbook(io.BytesIO(r.data))
    wb["Rows"].cell(row=2, column=FIRST + 1).value = 2.5
    key = wb["Rows"].cell(row=2, column=KEY).value
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    up = c.post("/api/scenario-sheet/upload", data={"file": (buf, "mine.xlsx")}, content_type="multipart/form-data")
    body = up.get_json()
    assert up.status_code == 200 and body["adjust"] == {"row": {key: {"promo_add_pp": 2.5}}}
    from engine import activity
    events, _ = activity.recent(50)
    mine = [e for e in events if e["action"] in ("sheet_download", "sheet_upload")]
    assert {e["action"] for e in mine} == {"sheet_download", "sheet_upload"}
    seen = " ".join(f"{e['action']} {e['detail']}" for e in mine)
    assert "Sample" not in seen and "2.5" not in seen              # events and counts, never names or values
    bad = c.post("/api/scenario-sheet/upload", data={"file": (io.BytesIO(b"nope"), "x.xlsx")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "Excel" in bad.get_json()["error"]
    assert c.post("/api/scenario-sheet/upload", data={}, content_type="multipart/form-data").status_code == 400


def test_a_saved_scenario_keeps_single_row_changes(world):
    from engine import scenarios
    out = _upload(_filled(edits=[(_key(), "promo_add_pp", 2)]))
    assert sim.parse_adjust(out["adjust"]) == out["adjust"]
    assert sim.split_levers({**out["levers"], "adjust": out["adjust"]})[1] == out["adjust"]


def test_the_sheet_routes_need_a_login(world):
    import app
    anon = app.app.test_client()
    assert anon.post("/api/scenario-sheet/download", data="{}", content_type="application/json").status_code in (401, 302, 403)
    assert anon.post("/api/scenario-sheet/upload", data={}, content_type="multipart/form-data").status_code in (401, 302, 403)
