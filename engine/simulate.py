"""Gross-to-net P&L and promo simulation.

Per row (chain x sales office x SKU), monthly run-rate:

    MRP value            units x MRP
    - trade margin       chain's share of MRP
    = Gross sales        invoice value to the chain
    - promo              scheme / discount on invoice value
    - rebates            display, listing and other fees on invoice value
    = Net revenue
    - COGS               per-unit cost, milk-linked share moves with milk cost
    = Gross margin
    - Logistics          per-unit freight x state factor
    - Sales and distribution   percent of net revenue
    = Contribution
"""
from dataclasses import dataclass, fields

from . import data

SD_PCT = 0.035
PROMO_RESPONSE_BOOST = 3.0  # promo elasticity as a multiple of everyday price elasticity
PUSH_PER_PP_TRADE = 0.008   # volume lift per +1 pp trade margin (shelf push)
PUSH_PER_PP_REBATE = 0.004  # volume lift per +1 pp rebate / display fees

METRICS = ("units", "mrp_value", "trade_margin", "gross", "promo", "rebates",
           "net", "cogs", "gm", "logistics", "sd", "cm")

# (min, max) the API will accept for each lever.
LIMITS = {
    "price_change_pct": (-15.0, 15.0),
    "promo_add_pp": (-5.0, 20.0),
    "promo_pass_through_pct": (0.0, 100.0),
    "trade_margin_add_pp": (-5.0, 8.0),
    "rebate_add_pp": (-3.0, 6.0),
    "elasticity_scale": (0.25, 3.0),
    "manual_uplift_pct": (-30.0, 50.0),
    "milk_cost_change_pct": (-20.0, 30.0),
    "freight_change_pct": (-20.0, 40.0),
}


@dataclass(frozen=True)
class Levers:
    price_change_pct: float = 0.0
    promo_add_pp: float = 0.0
    promo_pass_through_pct: float = 70.0
    trade_margin_add_pp: float = 0.0
    rebate_add_pp: float = 0.0
    elasticity_scale: float = 1.0
    manual_uplift_pct: float = 0.0
    milk_cost_change_pct: float = 0.0
    freight_change_pct: float = 0.0


DEFAULT_LEVERS = Levers()


def parse_levers(raw):
    raw = raw or {}
    values = {}
    for f in fields(Levers):
        lo, hi = LIMITS[f.name]
        try:
            v = float(raw.get(f.name, getattr(DEFAULT_LEVERS, f.name)))
        except (TypeError, ValueError):
            v = getattr(DEFAULT_LEVERS, f.name)
        values[f.name] = min(hi, max(lo, v))
    return Levers(**values)


def parse_scope(raw):
    """Empty or missing list for a dimension means 'all'."""
    raw = raw or {}
    return {
        "chain": set(raw.get("chains") or []),
        "state": set(raw.get("states") or []),
        "sku": set(raw.get("skus") or []),
    }


def in_scope(row, scope):
    return all(not scope[d] or row[d] in scope[d] for d in ("chain", "state", "sku"))


def calc_row(row, lv, active, fixed_volume=False):
    """Metrics for one row. Commercial levers only apply when `active`;
    cost levers always apply. `fixed_volume` freezes units at baseline so the
    rate effect can be separated from the volume response."""
    chain = data.CHAIN_BY_ID[row["chain"]]
    sku = data.SKU_BY_ID[row["sku"]]
    state = data.STATE_BY_ID[row["state"]]

    price = lv.price_change_pct / 100 if active else 0.0
    promo_add = lv.promo_add_pp / 100 if active else 0.0
    trade_add = lv.trade_margin_add_pp / 100 if active else 0.0
    rebate_add = lv.rebate_add_pp / 100 if active else 0.0

    mrp = sku["mrp"] * (1 + price)
    trade = min(0.4, max(0.0, chain["trade_margin"] + trade_add))
    promo_rate = max(0.0, chain["base_promo"] + promo_add)
    rebate_rate = max(0.0, chain["rebate"] + rebate_add)
    invoice = mrp * (1 - trade)

    units = row["units"]
    if active and not fixed_volume:
        promo_delta = promo_rate - chain["base_promo"]
        promo_ratio = 1 - lv.promo_pass_through_pct / 100 * promo_delta * (1 - chain["trade_margin"])
        e = sku["elasticity"] * lv.elasticity_scale
        # Shoppers react harder to a temporary promo than to a permanent price change.
        mult = (1 + price) ** e * promo_ratio ** (e * PROMO_RESPONSE_BOOST)
        mult *= 1 + PUSH_PER_PP_TRADE * (trade - chain["trade_margin"]) * 100
        mult *= 1 + PUSH_PER_PP_REBATE * (rebate_rate - chain["rebate"]) * 100
        mult *= 1 + lv.manual_uplift_pct / 100
        units = units * max(0.0, mult)

    mrp_value = units * mrp
    trade_margin = mrp_value * trade
    gross = units * invoice
    promo = gross * promo_rate
    rebates = gross * rebate_rate
    net = gross - promo - rebates
    cogs = units * sku["cogs"] * (1 + sku["milk_share"] * lv.milk_cost_change_pct / 100)
    gm = net - cogs
    logistics = (units * sku["freight"] * state["freight_factor"]
                 * (1 + lv.freight_change_pct / 100))
    sd = net * SD_PCT
    cm = gm - logistics - sd
    return {"units": units, "mrp_value": mrp_value, "trade_margin": trade_margin,
            "gross": gross, "promo": promo, "rebates": rebates, "net": net,
            "cogs": cogs, "gm": gm, "logistics": logistics, "sd": sd, "cm": cm}


def total(items):
    out = dict.fromkeys(METRICS, 0.0)
    for m in items:
        for k in METRICS:
            out[k] += m[k]
    return out


def derive(m):
    """Adds ratios to a metrics dict (returns a new dict)."""
    out = dict(m)
    out["gm_pct"] = m["gm"] / m["net"] if m["net"] else 0.0
    out["cm_pct"] = m["cm"] / m["net"] if m["net"] else 0.0
    out["net_per_unit"] = m["net"] / m["units"] if m["units"] else 0.0
    return out


BASE_METRICS = [calc_row(r, DEFAULT_LEVERS, False) for r in data.ROWS]


def scenario_metrics(lv, scope):
    return [calc_row(r, lv, in_scope(r, scope)) for r in data.ROWS]


def bridge(lv, scope):
    """Contribution bridge from baseline to scenario.

    Levers are layered on one group at a time with volume held at baseline,
    so each step is a pure rate effect; whatever is left is the volume response.
    """
    def cm_for(partial):
        cand = Levers(**{**DEFAULT_LEVERS.__dict__,
                         "promo_pass_through_pct": lv.promo_pass_through_pct,
                         "elasticity_scale": lv.elasticity_scale, **partial})
        return sum(calc_row(r, cand, in_scope(r, scope), fixed_volume=True)["cm"]
                   for r in data.ROWS)

    groups = [
        ("price", "Price", {"price_change_pct": lv.price_change_pct}),
        ("promo", "Promo", {"promo_add_pp": lv.promo_add_pp}),
        ("trade", "Trade terms", {"trade_margin_add_pp": lv.trade_margin_add_pp,
                                  "rebate_add_pp": lv.rebate_add_pp}),
        ("cost", "Costs", {"milk_cost_change_pct": lv.milk_cost_change_pct,
                           "freight_change_pct": lv.freight_change_pct}),
    ]
    base_cm = sum(m["cm"] for m in BASE_METRICS)
    full_cm = sum(m["cm"] for m in scenario_metrics(lv, scope))

    steps = [{"key": "baseline", "label": "Baseline", "kind": "total", "value": base_cm}]
    applied, prev = {}, base_cm
    for key, label, partial in groups:
        applied.update(partial)
        cur = cm_for(applied)
        steps.append({"key": key, "label": label, "kind": "delta", "value": cur - prev})
        prev = cur
    steps.append({"key": "volume", "label": "Volume response", "kind": "delta",
                  "value": full_cm - prev})
    steps.append({"key": "scenario", "label": "Scenario", "kind": "total", "value": full_cm})
    return steps


DIM_LABELS = {
    "chain": lambda k: data.CHAIN_BY_ID[k]["name"],
    "state": lambda k: data.STATE_BY_ID[k]["name"],
    "so": lambda k: data.SO_BY_ID[k]["name"],
    "sku": lambda k: data.SKU_BY_ID[k]["name"],
}
DIMS = tuple(DIM_LABELS)


def _group(items, dim):
    groups = {}
    for it in items:
        groups.setdefault(it["row"][dim], []).append(it)
    return groups


def breakdown(items, dim):
    out = []
    for key, grp in _group(items, dim).items():
        out.append({"id": key, "label": DIM_LABELS[dim](key),
                    "base": derive(total(g["base"] for g in grp)),
                    "sim": derive(total(g["sim"] for g in grp))})
    out.sort(key=lambda n: n["sim"]["net"], reverse=True)
    return out


def _tree(items, dims, level, path):
    dim = dims[level]
    nodes = []
    for key, grp in _group(items, dim).items():
        node_id = f"{path}/{dim}:{key}"
        node = {"id": node_id, "dim": dim, "label": DIM_LABELS[dim](key),
                "base": derive(total(g["base"] for g in grp)),
                "sim": derive(total(g["sim"] for g in grp))}
        if level + 1 < len(dims):
            node["children"] = _tree(grp, dims, level + 1, node_id)
        nodes.append(node)
    nodes.sort(key=lambda n: n["sim"]["net"], reverse=True)
    return nodes


def _items(lv, scope):
    sims = scenario_metrics(lv, scope)
    return [{"row": r, "base": b, "sim": s}
            for r, b, s in zip(data.ROWS, BASE_METRICS, sims)]


def simulate(levers_raw, scope_raw):
    lv, scope = parse_levers(levers_raw), parse_scope(scope_raw)
    items = _items(lv, scope)
    return {
        "base": derive(total(i["base"] for i in items)),
        "sim": derive(total(i["sim"] for i in items)),
        "bridge": bridge(lv, scope),
        "breakdown": {d: breakdown(items, d) for d in ("chain", "state", "sku")},
        "rows_in_scope": sum(1 for r in data.ROWS if in_scope(r, scope)),
        "rows_total": len(data.ROWS),
    }


def pnl(levers_raw, scope_raw, dims):
    lv, scope = parse_levers(levers_raw), parse_scope(scope_raw)
    dims = [d for d in (dims or []) if d in DIMS]
    dims = list(dict.fromkeys(dims)) or ["chain", "state", "sku"]
    items = _items(lv, scope)
    return {
        "dims": dims,
        "total": {"base": derive(total(i["base"] for i in items)),
                  "sim": derive(total(i["sim"] for i in items))},
        "tree": _tree(items, dims, 0, ""),
    }
