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


# ---------- breakeven and the response range ----------
def base_cm():
    return sum(m["cm"] for m in sim.BASE_METRICS)


def cm_with_uplift(levers, scope, uplift):
    """Contribution if every pack in scope rises by `uplift`, built row by row at the scenario's terms.
    Independent of breakeven(): it only uses calc_row."""
    lv, sc = sim.parse_levers(levers), sim.parse_scope(scope)
    total = 0.0
    for r in data.ROWS:
        active = sim.in_scope(r, sc)
        row = dict(r, units=r["units"] * (1 + uplift)) if active else r
        total += sim.calc_row(row, lv, active, fixed_volume=True)["cm"]
    return total


def test_breakeven_is_neutral_when_nothing_changes():
    b = sim.simulate({}, {})["breakeven"]
    assert b["status"] == "neutral" and b["required"] is None


def test_extra_visibility_alone_has_nothing_to_pay_back():
    # it adds volume but changes no price, promo or cost, so breakeven has nothing to say
    out = sim.simulate({"manual_uplift_pct": 6}, {})
    assert out["breakeven"]["status"] == "neutral"
    assert out["sim"]["units"] > out["base"]["units"]


@pytest.mark.parametrize("levers", [
    {"promo_add_pp": 5},
    {"price_change_pct": 3},
    {"milk_cost_change_pct": 5},
    {"trade_margin_add_pp": 2, "rebate_add_pp": 1},
    {"promo_add_pp": 3, "price_change_pct": 2, "freight_change_pct": 10},
])
@pytest.mark.parametrize("scope", [{}, "one chain"])
def test_breakeven_volume_brings_contribution_back_to_baseline(levers, scope):
    scope = {"chain": [first("chain")]} if scope == "one chain" else scope
    b = sim.simulate(levers, scope)["breakeven"]
    assert b["status"] in ("needs", "ahead")
    assert close(cm_with_uplift(levers, scope, b["required"]), base_cm(), tol=1e-9)


def test_a_promo_needs_extra_volume_and_the_model_may_not_supply_it():
    b = sim.simulate({"promo_add_pp": 5}, {})["breakeven"]
    assert b["status"] == "needs" and b["required"] > 0 and b["extra_units"] > 0
    assert b["covered"] is False and b["gap"] < 0
    assert close(b["gap"], b["assumed"] - b["required"])


def test_a_price_rise_is_ahead_and_can_lose_volume():
    b = sim.simulate({"price_change_pct": 3}, {})["breakeven"]
    assert b["status"] == "ahead" and b["required"] < 0
    assert b["assumed"] < 0 and b["covered"] is True


def test_breakeven_is_unreachable_when_each_extra_pack_loses_money():
    b = sim.simulate({"promo_add_pp": 20, "trade_margin_add_pp": 8, "rebate_add_pp": 6}, {})["breakeven"]
    assert b["status"] == "unreachable" and b["required"] is None


def test_breakeven_counts_only_the_lines_in_scope():
    chain = first("chain")
    b = sim.simulate({"promo_add_pp": 5}, {"chain": [chain]})["breakeven"]
    in_scope_units = sum(r["units"] for r in data.ROWS if r["chain"] == chain)
    all_units = sum(r["units"] for r in data.ROWS)
    assert close(b["units_in_scope"], in_scope_units) and b["units_in_scope"] < all_units


def test_breakeven_with_nothing_in_scope_is_neutral():
    b = sim.simulate({"promo_add_pp": 5}, {"chain": ["no-such-chain"]})["breakeven"]
    assert b["status"] == "neutral" and b["units_in_scope"] == 0


def test_response_range_base_case_is_the_scenario():
    out = sim.simulate({"promo_add_pp": 3, "price_change_pct": -1}, {})
    base = [c for c in out["range"]["cases"] if c["key"] == "base"][0]
    assert close(base["cm"], out["sim"]["cm"]) and close(base["units"], out["sim"]["units"])


def test_response_range_scales_the_volume_change():
    out = sim.simulate({"promo_add_pp": 3}, {})
    by = {c["key"]: c for c in out["range"]["cases"]}
    b0 = out["base"]["units"]
    change = by["base"]["units"] - b0
    assert change > 0
    assert close(by["weak"]["units"] - b0, 0.5 * change) and close(by["strong"]["units"] - b0, 1.5 * change)


def test_response_range_is_flat_when_nothing_changes():
    rng = sim.simulate({}, {})["range"]
    assert all(abs(c["d_cm"]) < 1e-6 and abs(c["d_units"]) < 1e-6 for c in rng["cases"])
    assert rng["improves"] == []


def test_response_range_says_which_cases_improve_contribution():
    assert sim.simulate({"price_change_pct": 3}, {})["range"]["improves"] == ["weak", "base", "strong"]
    assert sim.simulate({"promo_add_pp": 5}, {})["range"]["improves"] == []


def test_cost_levers_do_not_depend_on_how_shoppers_react():
    cms = {c["cm"] for c in sim.simulate({"milk_cost_change_pct": 5}, {})["range"]["cases"]}
    assert len(cms) == 1


def test_a_stronger_response_never_makes_volume_negative():
    lv = sim.parse_levers({"price_change_pct": 15, "elasticity_scale": 3, "promo_add_pp": -5,
                           "manual_uplift_pct": -30})
    assert all(sim.calc_row(r, lv, True, response=1.5)["units"] >= 0 for r in data.ROWS)
