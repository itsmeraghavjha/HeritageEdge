import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import simulate as sim  # noqa: E402


def close(a, b, tol=1e-6):
    return math.isclose(a, b, rel_tol=tol, abs_tol=1e-6)


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
    assert close(m["cm"], m["gm"] - m["logistics"] - m["sd"])


def test_price_rise_cuts_volume_raises_realisation():
    out = sim.simulate({"price_change_pct": 5}, {})
    assert out["sim"]["units"] < out["base"]["units"]
    assert out["sim"]["net_per_unit"] > out["base"]["net_per_unit"]


def test_promo_adds_volume_but_costs_margin_rate():
    out = sim.simulate({"promo_add_pp": 5}, {})
    assert out["sim"]["units"] > out["base"]["units"]
    assert out["sim"]["promo"] > out["base"]["promo"]
    assert out["sim"]["gm_pct"] < out["base"]["gm_pct"]


def test_milk_cost_hits_cogs_everywhere_even_with_scope():
    out = sim.simulate({"milk_cost_change_pct": 10}, {"chains": ["dmart"]})
    assert out["sim"]["cogs"] > out["base"]["cogs"]
    other = [c for c in out["breakdown"]["chain"] if c["id"] == "metro"][0]
    assert other["sim"]["cogs"] > other["base"]["cogs"]


def test_scope_limits_commercial_levers():
    out = sim.simulate({"price_change_pct": 8}, {"chains": ["dmart"]})
    by = {c["id"]: c for c in out["breakdown"]["chain"]}
    assert not close(by["dmart"]["sim"]["net"], by["dmart"]["base"]["net"])
    assert close(by["metro"]["sim"]["net"], by["metro"]["base"]["net"])


def test_bridge_sums_to_scenario():
    out = sim.simulate({"price_change_pct": 3, "promo_add_pp": 4,
                        "trade_margin_add_pp": 1, "milk_cost_change_pct": 5}, {})
    steps = out["bridge"]
    assert close(steps[0]["value"] + sum(s["value"] for s in steps[1:-1]), steps[-1]["value"])
    assert close(steps[-1]["value"], out["sim"]["cm"])


def test_tree_children_sum_to_parent():
    res = sim.pnl({"promo_add_pp": 3}, {}, ["chain", "state", "sku"])
    for node in res["tree"]:
        for key in ("net", "cm", "units"):
            assert close(node["sim"][key], sum(c["sim"][key] for c in node["children"]))
    for key in ("net", "cm"):
        assert close(res["total"]["sim"][key], sum(n["sim"][key] for n in res["tree"]))


def test_inputs_are_clamped():
    lv = sim.parse_levers({"price_change_pct": 999, "milk_cost_change_pct": "abc"})
    assert lv.price_change_pct == sim.LIMITS["price_change_pct"][1]
    assert lv.milk_cost_change_pct == 0.0
