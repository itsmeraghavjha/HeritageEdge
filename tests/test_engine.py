import math

import pytest

from engine import data, simulate as sim


def first(dim):
    return data.OPTIONS[dim][0]["id"]


def last(dim):
    return data.OPTIONS[dim][-1]["id"]


def close(a, b, tol=1e-6):
    return math.isclose(a, b, rel_tol=tol, abs_tol=1e-6)


def test_tests_run_on_sample_data():
    # guards against a test run quietly using real company data
    assert data.MODE == "sample"


def test_neutral_levers_match_baseline():
    out = sim.simulate({}, {})
    for k in sim.METRICS:
        assert close(out["base"][k], out["sim"][k])
    assert all(abs(s["value"]) < 1e-6 for s in out["bridge"] if s["kind"] == "delta")


def test_pnl_lines_add_up():
    m = sim.simulate({}, {})["base"]
    assert close(m["gross"], m["mrp_value"] - m["trade_margin"])
    assert close(m["net"], m["gross"] - m["promo"] - m["rebates"])
    assert close(m["gm"], m["net"] - m["cogs"])
    assert close(m["cm"], m["gm"] - m["logistics"])
    assert close(m["nm"], m["cm"] - m["pa"] - m["interest"] - m["ou"] - m["mktg"])


def test_price_rise_cuts_volume_raises_realisation():
    out = sim.simulate({"price_change_pct": 5}, {})
    assert out["sim"]["units"] < out["base"]["units"]
    assert out["sim"]["net_per_unit"] > out["base"]["net_per_unit"]


def test_promo_adds_volume_but_costs_margin_rate():
    out = sim.simulate({"promo_add_pp": 5}, {})
    assert out["sim"]["units"] > out["base"]["units"]
    assert out["sim"]["promo"] > out["base"]["promo"]
    assert out["sim"]["gm_pct"] < out["base"]["gm_pct"]


def test_attributed_costs_follow_their_basis():
    # On one line, volume-based costs move with packs sold and revenue-based costs move with net revenue.
    # (Across many lines the ratios also shift with mix, so this is checked line by line.)
    row = max(data.ROWS, key=lambda r: r["units"])
    before = sim.calc_row(row, sim.DEFAULT_LEVERS, False)
    after = sim.calc_row(row, sim.parse_levers({"price_change_pct": 5}), True)
    assert after["units"] != before["units"]
    for line in ("cogs", "sd", "ou"):
        assert data.BASIS[line] == "volume"
    for line in ("pa", "interest", "mktg"):
        assert data.BASIS[line] == "revenue"
        assert close(after[line] / after["net"], before[line] / before["net"])
    assert close(after["ou"] / after["units"], before["ou"] / before["units"])


def test_milk_cost_hits_cogs_everywhere_even_with_scope():
    out = sim.simulate({"milk_cost_change_pct": 10}, {"chain": [first("chain")]})
    other = [c for c in out["breakdown"]["chain"] if c["id"] == last("chain")][0]
    assert out["sim"]["cogs"] > out["base"]["cogs"]
    assert other["sim"]["cogs"] > other["base"]["cogs"]


def test_scope_limits_commercial_levers():
    c1, c2 = first("chain"), last("chain")
    out = sim.simulate({"price_change_pct": 8}, {"chain": [c1]})
    by = {c["id"]: c for c in out["breakdown"]["chain"]}
    assert not close(by[c1]["sim"]["net"], by[c1]["base"]["net"])
    assert close(by[c2]["sim"]["net"], by[c2]["base"]["net"])


@pytest.mark.parametrize("dim", data.DIM_IDS)
def test_scope_works_on_every_dimension(dim):
    out = sim.simulate({"price_change_pct": 4}, {dim: [first(dim)]})
    assert 0 < out["rows_in_scope"] <= out["rows_total"]
    if len(data.OPTIONS[dim]) > 1:
        assert out["rows_in_scope"] < out["rows_total"]
        # packs sold always change with price. (Net revenue need not: at an elasticity of exactly -1 it is unchanged.)
        assert not close(out["sim"]["units"], out["base"]["units"])


def test_unknown_scope_id_changes_nothing():
    out = sim.simulate({"price_change_pct": 8}, {"chain": ["no-such-chain"]})
    assert out["rows_in_scope"] == 0
    assert close(out["sim"]["net"], out["base"]["net"])


def test_bridge_sums_to_scenario():
    out = sim.simulate({"price_change_pct": 3, "promo_add_pp": 4,
                        "trade_margin_add_pp": 1, "milk_cost_change_pct": 5}, {})
    steps = out["bridge"]
    assert close(steps[0]["value"] + sum(s["value"] for s in steps[1:-1]), steps[-1]["value"])
    assert close(steps[-1]["value"], out["sim"]["cm"])


def test_tree_children_sum_to_parent():
    res = sim.pnl({"promo_add_pp": 3}, {}, ["chain", "state", "city", "sku"])
    assert res["dims"] == ["chain", "state", "city", "sku"]
    for node in res["tree"]:
        for key in ("net", "cm", "nm", "units"):
            assert close(node["sim"][key], sum(c["sim"][key] for c in node["children"]))
    for key in ("net", "cm", "nm"):
        assert close(res["total"]["sim"][key], sum(n["sim"][key] for n in res["tree"]))


def test_every_dimension_can_group_the_pnl():
    for dim in data.DIM_IDS:
        res = sim.pnl({}, {}, [dim])
        assert res["tree"], dim
        assert close(res["total"]["base"]["net"], sum(n["base"]["net"] for n in res["tree"]))


def test_inputs_are_clamped():
    lv = sim.parse_levers({"price_change_pct": 999, "milk_cost_change_pct": "abc"})
    assert lv.price_change_pct == sim.LIMITS["price_change_pct"][1]
    assert lv.milk_cost_change_pct == 0.0


def test_pnl_filter_limits_rows_and_totals():
    full = sim.pnl({}, {}, ["chain"])
    part = sim.pnl({}, {}, ["chain"], {"city": [first("city")]})
    assert 0 < part["rows_shown"] < full["rows_shown"] == part["rows_total"]
    assert part["total"]["sim"]["net"] < full["total"]["sim"]["net"]
    assert close(part["total"]["sim"]["net"], sum(n["sim"]["net"] for n in part["tree"]))


def test_pnl_filter_does_not_change_scenario_per_row():
    # filtering is a view: a state's numbers are the same with or without a state filter
    lv = {"price_change_pct": 3}
    state = first("state")
    a = sim.pnl(lv, {}, ["state"], {"state": [state]})["tree"][0]["sim"]["net"]
    b = [n for n in sim.pnl(lv, {}, ["state"])["tree"] if n["id"].endswith(state)][0]["sim"]["net"]
    assert close(a, b)


def test_filters_on_several_dimensions_combine():
    one = sim.pnl({}, {}, ["chain"], {"grp": [first("grp")]})["rows_shown"]
    both = sim.pnl({}, {}, ["chain"], {"grp": [first("grp")], "state": [first("state")]})["rows_shown"]
    assert 0 < both <= one


def test_old_style_plural_keys_are_ignored_not_misread():
    # an old client sending {"chains": [...]} must not silently filter on something else
    out = sim.simulate({"price_change_pct": 5}, {"chains": [first("chain")]})
    assert out["rows_in_scope"] == out["rows_total"]
