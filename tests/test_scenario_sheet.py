"""The scenario sheet: download the rows as Excel with a Baseline and a Scenario column for five drivers, type or
calculate the scenario there, upload it back. The sheet does no maths: each changed Scenario value becomes the change the
engine already takes for that row, so the key tests are that the engine reproduces exactly what was typed and that a
sheet gives the same numbers as the same change set on screen. Synthetic sample data only."""
import io
import json

import pytest
from openpyxl import Workbook, load_workbook

from engine import access, data, scenario_sheet as ss
from engine import simulate as sim
from tests.test_kam import client_for, env, world  # noqa: F401  (fixtures)

N_DIMS = len(data.DIM_DEFS)


def _cols(ws):
    """{header: 1-based column}"""
    return {str(c.value): c.column for c in ws[1]}


def _sheet(levers=None, allowed=None, limited=False, scope=None):
    wb = load_workbook(ss.build_template(levers, allowed, limited, scope))
    ws = wb["Rows"]
    cols = _cols(ws)
    where = {str(ws.cell(row=r, column=cols["Key"]).value): r for r in range(2, ws.max_row + 1)}
    return wb, ws, cols, where


def _set(ws, cols, where, key, driver, value):
    ws.cell(row=where[key], column=cols[f"Scenario: {ss.DRIVER_TITLE[driver]}"]).value = value


def _upload(wb, allowed=None):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return ss.read_upload(buf, allowed)


def _payload(out):
    return {**out["levers"], "adjust": out["adjust"]}


def _key(i=0):
    return data.ROWS[i]["rowkey"]


def test_an_untouched_sheet_means_no_change():
    wb, *_ = _sheet()
    out = _upload(wb)
    assert out["adjust"] == {} and all(v == 0 for v in out["levers"].values())
    assert out["report"]["changed_rows"] == 0 and out["report"]["levers_set"] == 0


def test_the_sheet_speaks_litres_and_has_no_packs_or_kg_columns():
    _, ws, cols, _ = _sheet()
    heads = " | ".join(cols)
    assert "packs" not in heads.lower() and "kg" not in heads.lower() and "KGPD" not in heads
    assert "Baseline: Volume per day (LPD)" in cols and sum("Volume" in h and "Baseline" in h for h in cols) == 2   # volume, volume per day
    assert [h for h in cols if h.startswith("Scenario:")] == [f"Scenario: {t}" for _, t, _ in ss.DRIVERS]
    # volume is litres, with kg counted as litres: one column for both
    for i in range(len(data.ROWS)):
        row = data.ROWS[i]
        t = ss._terms(row, sim.DEFAULT_LEVERS)
        assert t["volume"] == pytest.approx(row["units"] * row["size"] / 1e3)


def test_each_driver_has_its_baseline_next_to_a_scenario_that_starts_equal():
    _, ws, cols, where = _sheet()
    line = where[_key(0)]
    row = data.ROWS[0]
    b = ss._terms(row, sim.DEFAULT_LEVERS)
    for k, title, _ in ss.DRIVERS:
        base_v = ws.cell(row=line, column=cols[f"Baseline: {title}"]).value
        scen_v = ws.cell(row=line, column=cols[f"Scenario: {title}"]).value
        assert base_v == pytest.approx(b[k]) and scen_v == pytest.approx(b[k])
        assert cols[f"Scenario: {title}"] == cols[f"Baseline: {title}"] + 1
    assert b["mrp"] == pytest.approx(row["mrp_x"] / row["size"]) and b["trade"] == pytest.approx(row["trade"] * 100)


def test_one_line_is_one_row_of_the_baseline_largest_first_and_the_p_and_l_matches():
    _, ws, cols, where = _sheet()
    assert ws.max_row - 1 == len(data.ROWS) and len(where) == len(data.ROWS)
    nets = [ws.cell(row=r, column=cols["Baseline: Net revenue"]).value for r in range(2, ws.max_row + 1)]
    assert nets == sorted(nets, reverse=True)
    assert sum(nets) == pytest.approx(sum(m["net"] for m in sim.base_metrics()) / 1e5)
    m = sim.derive(sim.base_metrics()[0])
    line = where[_key(0)]
    assert ws.cell(row=line, column=cols["Baseline: Volume per day (LPD)"]).value == pytest.approx((m["litres"] + m["kg"]) / data.days_per_month())
    assert ws.cell(row=line, column=cols["Baseline: NM %"]).value == pytest.approx(m["nm_pct"] / 0.01)
    assert ws.auto_filter.ref and ws.column_dimensions[ws.cell(row=1, column=cols["Key"]).column_letter].hidden


def test_twenty_percent_more_volume_on_one_row_moves_only_that_row():
    i = 0
    wb, ws, cols, where = _sheet()
    base_vol = ss._terms(data.ROWS[i], sim.DEFAULT_LEVERS)["volume"]
    _set(ws, cols, where, _key(i), "volume", base_vol * 1.2)                    # what =baseline*1.2 holds once Excel has worked it out
    out = _upload(wb)
    assert out["adjust"] == {"row": {_key(i): {"volume_change_pct": pytest.approx(20.0)}}}
    sims = sim.scenario_metrics(sim.parse_levers(out["levers"]), sim.parse_scope({}), adjust=out["adjust"])
    base = sim.base_metrics()
    assert [j for j, (a, b) in enumerate(zip(base, sims)) if a != b] == [i]
    assert sims[i]["units"] == pytest.approx(base[i]["units"] * 1.2)
    assert sims[i]["litres"] + sims[i]["kg"] == pytest.approx((base[i]["litres"] + base[i]["kg"]) * 1.2)


@pytest.mark.parametrize("driver,factor", [("promo", 1.5), ("other", 0.5), ("mrp", 1.05), ("trade", 1.1)])
def test_the_engine_lands_exactly_on_the_scenario_value_typed(driver, factor):
    i = 3
    row = data.ROWS[i]
    wb, ws, cols, where = _sheet()
    want = ss._terms(row, sim.DEFAULT_LEVERS)[driver] * factor
    _set(ws, cols, where, _key(i), driver, want)
    out = _upload(wb)
    lv = sim.make_resolver(sim.parse_levers(out["levers"]), out["adjust"])(row)
    assert ss._terms(row, lv)[driver] == pytest.approx(want)                    # the same number comes back out of the engine
    assert not out["report"]["clamped"]


def test_a_new_price_with_a_promo_in_rupees_gives_that_promo_per_litre():
    i = 3
    row = data.ROWS[i]
    wb, ws, cols, where = _sheet()
    b = ss._terms(row, sim.DEFAULT_LEVERS)
    _set(ws, cols, where, _key(i), "mrp", b["mrp"] * 1.04)
    _set(ws, cols, where, _key(i), "trade", b["trade"] + 1)
    _set(ws, cols, where, _key(i), "promo", b["promo"] * 1.3)
    _set(ws, cols, where, _key(i), "other", b["other"] + 0.1)
    out = _upload(wb)
    t = ss._terms(row, sim.make_resolver(sim.parse_levers(out["levers"]), out["adjust"])(row))
    assert (t["mrp"], t["trade"], t["promo"], t["other"]) == pytest.approx((b["mrp"] * 1.04, b["trade"] + 1, b["promo"] * 1.3, b["other"] + 0.1))


def test_what_is_set_on_the_screen_is_already_in_the_scenario_columns_and_gives_the_same_numbers():
    chain, sku = str(data.ROWS[0]["chain"]), str(data.ROWS[0]["sku"])
    now = {"price_change_pct": 1, "volume_change_pct": 5, "promo_add_pp": 1, "freight_change_pct": 5, "cogs_change_pct": -2,
           "adjust": {"chain": {chain: {"rebate_add_pp": 2}}, "sku": {sku: {"promo_add_pp": 1, "volume_change_pct": 3}}}}
    wb, *_ = _sheet(now)
    out = _upload(wb)
    assert out["levers"]["freight_change_pct"] == 5 and out["levers"]["cogs_change_pct"] == -2
    assert all(out["levers"][k] == 0 for k in ("price_change_pct", "promo_add_pp", "volume_change_pct"))   # now carried per row
    mine, theirs = sim.simulate(_payload(out), {}), sim.simulate(now, {})
    for key, v in theirs["sim"].items():
        assert mine["sim"][key] == pytest.approx(v), key


def test_rows_outside_the_scope_on_screen_stay_at_baseline_in_the_scenario_columns():
    chain = str(data.ROWS[0]["chain"])
    scope = {"chain": [chain]}
    now = {"volume_change_pct": 10}
    wb, ws, cols, where = _sheet(now, scope=scope)
    for r in data.ROWS:
        line = where[r["rowkey"]]
        t = ss._terms(r, sim.DEFAULT_LEVERS)
        v = ws.cell(row=line, column=cols[f"Scenario: {ss.DRIVER_TITLE['volume']}"]).value
        assert v == pytest.approx(t["volume"] * (1.1 if str(r["chain"]) == chain else 1.0))
    out = _upload(wb)
    mine, theirs = sim.simulate(_payload(out), scope), sim.simulate(now, scope)       # same rows reported on both sides
    for key, v in theirs["sim"].items():
        assert mine["sim"][key] == pytest.approx(v), key


def test_the_levers_sheet_has_only_the_two_cost_levers():
    wb, *_ = _sheet()
    ws = wb["Levers"]
    codes = [ws.cell(row=r, column=5).value for r in range(2, ws.max_row + 1)]
    assert codes == ["cogs_change_pct", "freight_change_pct"]
    ws.cell(row=2, column=2).value = 4
    ws.cell(row=3, column=2).value = -3
    out = _upload(wb)
    assert {k: v for k, v in out["levers"].items() if v} == {"cogs_change_pct": 4.0, "freight_change_pct": -3.0}
    assert set(out["levers"]) == set(sim.LIMITS) and out["report"]["levers_set"] == 2


def test_values_beyond_what_the_engine_allows_are_brought_back_and_reported():
    wb, ws, cols, where = _sheet()
    _set(ws, cols, where, _key(), "volume", ss._terms(data.ROWS[0], sim.DEFAULT_LEVERS)["volume"] * 3)     # +200%, the limit is +100%
    out = _upload(wb)
    assert out["adjust"]["row"][_key()]["volume_change_pct"] == sim.LIMITS["volume_change_pct"][1]
    assert any("Volume change" in c and "+200" in c for c in out["report"]["clamped"])


def test_a_cell_that_is_not_a_number_stops_the_upload_and_names_the_cell():
    wb, ws, cols, where = _sheet()
    _set(ws, cols, where, _key(), "promo", "three")
    with pytest.raises(ss.SheetError) as exc:
        _upload(wb)
    assert "Rows sheet" in str(exc.value) and "three" in str(exc.value)


def test_numbers_stored_as_text_are_accepted():
    wb, ws, cols, where = _sheet()
    base = ss._terms(data.ROWS[0], sim.DEFAULT_LEVERS)["trade"]
    _set(ws, cols, where, _key(), "trade", f" {base + 2} ")
    assert _upload(wb)["adjust"]["row"][_key()]["trade_margin_add_pp"] == pytest.approx(2.0)


def test_a_blank_scenario_cell_means_baseline():
    wb, ws, cols, where = _sheet()
    for k, *_ in ss.DRIVERS:
        _set(ws, cols, where, _key(), k, None)
    assert _upload(wb)["adjust"] == {}


def test_a_line_that_is_not_a_row_of_the_baseline_is_ignored_and_reported():
    wb, ws, cols, where = _sheet()
    extra = [None] * (ws.max_column)
    extra[cols[f"Scenario: {ss.DRIVER_TITLE['volume']}"] - 1] = 5
    extra[cols["Key"] - 1] = "no|such|row"
    ws.append(extra)
    out = _upload(wb)
    assert out["adjust"] == {} and any("Rows sheet" in i for i in out["report"]["ignored"])


def test_changing_the_baseline_columns_does_nothing_on_upload():
    wb, ws, cols, where = _sheet()
    for r in range(2, ws.max_row + 1):
        for h, c in cols.items():
            if h.startswith("Baseline:"):
                ws.cell(row=r, column=c).value = 12345
    assert _upload(wb)["adjust"] == {}


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
    wb, ws, cols, _ = _sheet()
    ws.delete_cols(cols["Key"])
    with pytest.raises(ss.SheetError) as exc:
        _upload(wb)
    assert "Key" in str(exc.value)


def test_a_kam_gets_only_their_own_rows_with_no_baseline_below_contribution_and_cannot_set_anyone_elses(world):
    from engine import users
    allowed = access.allowed_indices(users.get_by_email("asha@example.com"))
    mine = {data.ROWS[i]["rowkey"] for i in allowed}
    wb, ws, cols, where = _sheet(allowed=allowed, limited=True)
    assert set(where) == mine and 0 < len(mine) < len(data.ROWS)
    assert "Baseline: Contribution (MC)" in cols
    assert not any(any(w in h for w in ("P&A", "Interest", "Marketing", "Net margin", "NM %")) for h in cols)
    other = next(r for r in data.ROWS if r["rowkey"] not in mine)
    full, fws, fcols, fwhere = _sheet()
    _set(fws, fcols, fwhere, other["rowkey"], "volume", ss._terms(other, sim.DEFAULT_LEVERS)["volume"] * 1.5)
    out = _upload(full, allowed)                                                # a full sheet edited to reach another row
    assert out["adjust"] == {} and out["report"]["ignored"]


def test_the_routes_download_and_upload_and_log_only_counts(world):
    c = client_for("ceo@example.com", "Ceo-pass-12345")
    r = c.post("/api/scenario-sheet/download", data=json.dumps({"levers": {"volume_change_pct": 10}, "scope": {}}),
               content_type="application/json")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Rows"]
    cols = _cols(ws)
    v = ws.cell(row=2, column=cols["Scenario: Volume ('000 L)"]).value
    base = ws.cell(row=2, column=cols["Baseline: Volume ('000 L)"]).value
    assert v == pytest.approx(base * 1.1)                                       # what was set on screen is already there
    ws.cell(row=2, column=cols["Scenario: Promo per litre (Rs)"]).value = ws.cell(row=2, column=cols["Baseline: Promo per litre (Rs)"]).value + 0.5
    key = ws.cell(row=2, column=cols["Key"]).value
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    up = c.post("/api/scenario-sheet/upload", data={"file": (buf, "mine.xlsx")}, content_type="multipart/form-data")
    body = up.get_json()
    assert up.status_code == 200 and key in body["adjust"]["row"] and "promo_add_pp" in body["adjust"]["row"][key]
    assert body["levers"]["volume_change_pct"] == 0                              # the 10% now sits on each row
    from engine import activity
    events, _ = activity.recent(50)
    mine = [e for e in events if e["action"] in ("sheet_download", "sheet_upload")]
    assert {e["action"] for e in mine} == {"sheet_download", "sheet_upload"}
    seen = " ".join(f"{e['action']} {e['detail']}" for e in mine)
    assert "Sample" not in seen and "0.5" not in seen                           # events and counts, never names or values
    bad = c.post("/api/scenario-sheet/upload", data={"file": (io.BytesIO(b"nope"), "x.xlsx")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "Excel" in bad.get_json()["error"]
    assert c.post("/api/scenario-sheet/upload", data={}, content_type="multipart/form-data").status_code == 400


def test_the_sheet_routes_need_a_login(world):
    import app
    anon = app.app.test_client()
    assert anon.post("/api/scenario-sheet/download", data="{}", content_type="application/json").status_code in (401, 302, 403)
    assert anon.post("/api/scenario-sheet/upload", data={}, content_type="multipart/form-data").status_code in (401, 302, 403)
