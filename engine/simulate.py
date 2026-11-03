"""Gross-to-net P&L and promo simulation.

Per row (chain x sales office x SKU), monthly run-rate:

    MRP value            units x MRP
    - trade margin       chain's share of MRP
    = Gross sales        invoice value to the chain
    - promo              scheme / discount on invoice value
    - rebates            display, listing and other fees on invoice value
    = Net revenue
    - COGS               per-pack cost from the MIS, scaled by the COGS change lever
    = Gross margin
    - Over/under         per pack, from the MIS
    - Sales and distribution   per pack, from the MIS
    = Contribution       the MIS definition (MC)
    - P&A, interest, marketing   percent of net revenue, from the MIS
    = Net margin
"""
from dataclasses import dataclass, fields, replace

from . import data

# Volume is something the user enters (volume_change_pct). The three constants below only feed the hint
# the screen shows next to it ("the model suggests about +X%"); they never change the numbers on their own.
PROMO_RESPONSE_BOOST = 3.0  # promo elasticity as a multiple of everyday price elasticity
PROMO_PASS_THROUGH = 0.7    # the part of a promo that reaches the shelf price
PUSH_PER_PP_TRADE = 0.008   # volume lift per +1 pp trade margin (shelf push)
PUSH_PER_PP_REBATE = 0.004  # volume lift per +1 pp rebate / display fees

# Volume changes shown in the table next to the breakeven. The volume the user entered is added to it.
VOLUME_LADDER = (-10.0, 0.0, 10.0, 20.0, 30.0)
NEUTRAL_RUPEES = 0.5        # a scenario that moves contribution by less than this at today's volume is "neutral"

METRICS = ("units", "litres", "kg", "mrp_value", "trade_margin", "gross", "promo", "rebates", "net", "cogs", "gm",
           "logistics", "cm", "pa", "interest", "ou", "mktg", "nm")

# (min, max) the API will accept for each lever.
LIMITS = {
    "price_change_pct": (-15.0, 15.0),
    "promo_add_pp": (-5.0, 20.0),
    "trade_margin_add_pp": (-5.0, 8.0),
    "rebate_add_pp": (-3.0, 6.0),
    "volume_change_pct": (-50.0, 100.0),
    "cogs_change_pct": (-20.0, 30.0),
    "freight_change_pct": (-20.0, 40.0),
}


@dataclass(frozen=True)
class Levers:
    price_change_pct: float = 0.0
    promo_add_pp: float = 0.0
    trade_margin_add_pp: float = 0.0
    rebate_add_pp: float = 0.0
    volume_change_pct: float = 0.0
    cogs_change_pct: float = 0.0
    freight_change_pct: float = 0.0


DEFAULT_LEVERS = Levers()

# Row adjustments: a lever can also be set on a single chain, product category, SKU or state. They are added to the
# screen's levers for the rows they match; where several match one row (a chain and a SKU, say) they add up.
ADJUST_DIMS = ("chain", "family", "sku", "state")
ADJUST_LEVERS = ("price_change_pct", "promo_add_pp", "trade_margin_add_pp", "rebate_add_pp", "volume_change_pct")


def parse_adjust(raw):
    """{dim: {id: {lever: number}}} -> the same, with unknown names dropped, numbers clamped and zeros removed."""
    out = {}
    for dim, by_id in (raw or {}).items():
        if dim not in ADJUST_DIMS or not isinstance(by_id, dict):
            continue
        for key, levers in by_id.items():
            if not isinstance(levers, dict):
                continue
            clean = {}
            for name in ADJUST_LEVERS:
                try:
                    v = float(levers.get(name, 0.0))
                except (TypeError, ValueError):
                    continue
                lo, hi = LIMITS[name]
                v = min(hi, max(lo, v))
                if v:
                    clean[name] = v
            if clean:
                out.setdefault(dim, {})[str(key)] = clean
    return out


def split_levers(raw):
    """The levers dict the API receives may carry an "adjust" entry. Returns (Levers, adjust)."""
    raw = raw or {}
    return parse_levers(raw), parse_adjust(raw.get("adjust"))


def has_volume_adjust(adjust):
    return any("volume_change_pct" in a for by_id in (adjust or {}).values() for a in by_id.values())


def make_resolver(lv, adjust, skip=()):
    """A function row -> the Levers that apply to that row: the screen's levers plus every adjustment that matches it.
    `skip` names levers whose adjustments are left out (the bridge adds them one group at a time)."""
    if not adjust:
        return lambda row: lv
    cache = {}

    def resolve(row):
        extra = {}
        for dim, by_id in adjust.items():
            a = by_id.get(str(row[dim]))
            if a:
                for k, v in a.items():
                    if k not in skip:
                        extra[k] = extra.get(k, 0.0) + v
        if not extra:
            return lv
        sig = tuple(sorted(extra.items()))
        got = cache.get(sig)
        if got is None:
            got = replace(lv, **{k: min(LIMITS[k][1], max(LIMITS[k][0], getattr(lv, k) + v)) for k, v in extra.items()})
            cache[sig] = got
        return got
    return resolve


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


def parse_selection(raw):
    """{dimension id: [ids]} -> {dimension id: set}. Empty or missing means 'all' for that dimension."""
    raw = raw or {}
    return {d: set(raw.get(d) or []) for d in data.DIM_IDS}


def matches(row, selection):
    return all(not selection[d] or row[d] in selection[d] for d in data.DIM_IDS)


# Scope: where the commercial levers apply. Filter: which rows the P&L reports (levers are unaffected).
parse_scope = parse_filter = parse_selection
in_scope = passes = matches


def calc_row(row, lv, active, fixed_volume=False):
    """Metrics for one row. Commercial levers only apply when `active`; cost levers always apply.
    `fixed_volume` freezes units at baseline so the rate effect can be separated from the volume change.
    Volume moves only by the volume the user entered (`volume_change_pct`), on rows in scope.

    Row terms come from the data: list price, trade margin, current offer (promo_rate),
    and other_rate, the calibrated gap between list invoice and what was actually billed."""
    price = lv.price_change_pct / 100 if active else 0.0
    promo_add = lv.promo_add_pp / 100 if active else 0.0
    trade_add = lv.trade_margin_add_pp / 100 if active else 0.0
    rebate_add = lv.rebate_add_pp / 100 if active else 0.0

    mrp = row["mrp_x"] * (1 + price)
    trade = min(0.4, max(0.0, row["trade"] + trade_add))
    promo_rate = max(0.0, row["promo_rate"] + promo_add)
    rebate_rate = max(-0.5, row["other_rate"] + rebate_add)
    invoice = mrp * (1 - trade)

    units = row["units"]
    if active and not fixed_volume:
        units = units * max(0.0, 1 + lv.volume_change_pct / 100)

    mrp_value = units * mrp
    trade_margin = mrp_value * trade
    gross = units * invoice
    promo = gross * promo_rate
    rebates = gross * rebate_rate
    net = gross - promo - rebates
    def cost(line):  # attributed MIS cost: per pack, or % of net revenue, depending on data/attribution.csv
        return units * row[line] if data.BASIS[line] == "volume" else net * row[line]

    cogs = cost("cogs") * (1 + lv.cogs_change_pct / 100)
    gm = net - cogs
    logistics = cost("sd") * (1 + lv.freight_change_pct / 100)
    ou = cost("ou")
    cm = gm - ou - logistics          # the MIS definition: over/under is taken off before contribution
    pa, interest, mktg = cost("pa"), cost("interest"), cost("mktg")
    nm = cm - pa - interest - mktg
    qty = units * row["size"]
    return {"units": units, "litres": qty if row["qty_unit"] == "L" else 0.0,
            "kg": qty if row["qty_unit"] == "kg" else 0.0, "mrp_value": mrp_value, "trade_margin": trade_margin,
            "gross": gross, "promo": promo, "rebates": rebates, "net": net,
            "cogs": cogs, "gm": gm, "logistics": logistics, "cm": cm,
            "pa": pa, "interest": interest, "ou": ou, "mktg": mktg, "nm": nm}


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
    out["nm_pct"] = m["nm"] / m["net"] if m["net"] else 0.0
    out["net_per_unit"] = m["net"] / m["units"] if m["units"] else 0.0
    return out


BASE_METRICS = []


def refresh():
    """Recompute the baseline. Call after data.reload()."""
    global BASE_METRICS
    BASE_METRICS = [calc_row(r, DEFAULT_LEVERS, False) for r in data.ROWS]


refresh()


def view_rows(scope):
    """The rows the simulator screen reports: the ones inside the selection (all rows when nothing is selected).
    Returns (rows, baseline metrics for those rows)."""
    keep = [i for i, r in enumerate(data.ROWS) if in_scope(r, scope)]
    return [data.ROWS[i] for i in keep], [BASE_METRICS[i] for i in keep]


def scenario_metrics(lv, scope, rows=None, adjust=None):
    resolve = make_resolver(lv, adjust)
    return [calc_row(r, resolve(r), in_scope(r, scope)) for r in (data.ROWS if rows is None else rows)]


def bridge(lv, scope, sims=None, rows=None, base=None, adjust=None):
    """Contribution bridge from baseline to scenario.

    Levers are layered on one group at a time with volume held at baseline,
    so each step is a pure rate effect; whatever is left is the volume you entered.
    """
    rows = data.ROWS if rows is None else rows
    base = BASE_METRICS if base is None else base

    def cm_for(partial):
        cand = Levers(**{**DEFAULT_LEVERS.__dict__, **partial})
        resolve = make_resolver(cand, adjust, skip=set(ADJUST_LEVERS) - set(partial))
        return sum(calc_row(r, resolve(r), in_scope(r, scope), fixed_volume=True)["cm"]
                   for r in rows)

    groups = [
        ("price", "Price", {"price_change_pct": lv.price_change_pct}),
        ("promo", "Promo", {"promo_add_pp": lv.promo_add_pp}),
        ("trade", "Trade terms", {"trade_margin_add_pp": lv.trade_margin_add_pp,
                                  "rebate_add_pp": lv.rebate_add_pp}),
        ("cost", "Costs", {"cogs_change_pct": lv.cogs_change_pct,
                           "freight_change_pct": lv.freight_change_pct}),
    ]
    base_cm = sum(m["cm"] for m in base)
    full_cm = sum(m["cm"] for m in (sims if sims is not None else scenario_metrics(lv, scope, rows, adjust)))

    steps = [{"key": "baseline", "label": "Baseline", "kind": "total", "value": base_cm}]
    applied, prev = {}, base_cm
    for key, label, partial in groups:
        applied.update(partial)
        cur = cm_for(applied)
        steps.append({"key": key, "label": label, "kind": "delta", "value": cur - prev})
        prev = cur
    steps.append({"key": "volume", "label": "Volume you expect", "kind": "delta",
                  "value": full_cm - prev})
    steps.append({"key": "scenario", "label": "Scenario", "kind": "total", "value": full_cm})
    return steps


def breakeven(lv, scope, sims=None, rows=None, base=None, adjust=None):
    """How much extra volume the scenario needs to match today's contribution.

    The levers change what each pack earns. This asks: at the new terms, how many more packs (the same
    percentage in every line the commercial levers apply to) would pay for that? It needs no elasticity, so
    it does not depend on any volume assumption. Cost levers hit every line, but only the lines
    in scope can sell more.

    status  neutral      nothing to pay back (no lever changes what a pack earns)
            ahead        already better at today's volume; `required` is negative: the volume that can be lost
            needs        `required` more volume is needed
            unreachable  contribution in scope is zero or negative, so more packs never pay it back
    """
    rows = data.ROWS if rows is None else rows
    base = BASE_METRICS if base is None else base
    inside = [in_scope(r, scope) for r in rows]
    sims = sims if sims is not None else scenario_metrics(lv, scope, rows, adjust)
    resolve = make_resolver(lv, adjust)
    fixed = [calc_row(r, resolve(r), a, fixed_volume=True) for r, a in zip(rows, inside)]
    base_cm = sum(m["cm"] for m in base)
    cm_in = sum(f["cm"] for f, a in zip(fixed, inside) if a)
    cm_out = sum(f["cm"] for f, a in zip(fixed, inside) if not a)
    units_in = sum(r["units"] for r, a in zip(rows, inside) if a)
    units_sim = sum(m["units"] for m, a in zip(sims, inside) if a)
    assumed = units_sim / units_in - 1 if units_in else 0.0
    rate_effect = cm_in + cm_out - base_cm

    out = {"status": "neutral", "required": None, "assumed": assumed, "covered": None, "gap": None,
           "extra_units": None, "units_in_scope": units_in, "rate_effect": rate_effect}
    if not units_in or abs(rate_effect) < NEUTRAL_RUPEES:
        return out
    if cm_in <= 0:
        out["status"] = "unreachable"
        return out
    required = (base_cm - cm_out) / cm_in - 1
    out.update(status="ahead" if required <= 0 else "needs", required=required,
               extra_units=required * units_in, gap=assumed - required, covered=assumed >= required - 1e-9)
    return out


def suggested_volume_change(lv, scope, rows=None, adjust=None):
    """What the model's elasticities say the volume change would be for the price, promo and chain-term levers.

    This is a hint, shown next to the volume the user enters. It is an assumption (elasticities by product family,
    a promo boost, shelf-push constants), not a forecast, and it never feeds the numbers unless the user adopts it.
    Returns the units-weighted change over the rows in scope, as a fraction (0.05 = +5%)."""
    resolve = make_resolver(lv, adjust, skip=("volume_change_pct",))
    num = den = 0.0
    for r in (data.ROWS if rows is None else rows):
        if not in_scope(r, scope):
            continue
        rl = resolve(r)
        price = rl.price_change_pct / 100
        promo_add, trade_add, rebate_add = rl.promo_add_pp / 100, rl.trade_margin_add_pp / 100, rl.rebate_add_pp / 100
        trade = min(0.4, max(0.0, r["trade"] + trade_add))
        promo_delta = max(0.0, r["promo_rate"] + promo_add) - r["promo_rate"]
        promo_ratio = 1 - PROMO_PASS_THROUGH * promo_delta * (1 - r["trade"])
        e = r["e"]
        mult = (1 + price) ** e * promo_ratio ** (e * PROMO_RESPONSE_BOOST)
        mult *= 1 + PUSH_PER_PP_TRADE * (trade - r["trade"]) * 100
        mult *= 1 + PUSH_PER_PP_REBATE * (max(-0.5, r["other_rate"] + rebate_add) - r["other_rate"]) * 100
        num += r["units"] * max(0.0, mult)
        den += r["units"]
    return num / den - 1 if den else 0.0


def volume_ladder(lv, scope, sims=None, rows=None, base=None, adjust=None):
    """The same scenario at a range of volume changes, against today's baseline.

    One row per step in VOLUME_LADDER, plus the volume the user entered (marked). This shows how much the answer
    depends on the volume, which is the number the user has to judge."""
    rows = data.ROWS if rows is None else rows
    inside = [in_scope(r, scope) for r in rows]
    base = total(BASE_METRICS if base is None else base)
    # With volume set on single rows there is no one entered figure: the marked case is the scenario's actual change.
    entered = lv.volume_change_pct
    if sims is not None and has_volume_adjust(adjust) and base["units"]:
        entered = round((total(sims)["units"] / base["units"] - 1) * 100, 1)
    steps = sorted(set(VOLUME_LADDER) | {entered})
    resolve = make_resolver(lv, adjust, skip=("volume_change_pct",))
    cases = []
    for v in steps:
        if v == entered and sims is not None:
            ms = sims
        else:
            cand = replace(lv, volume_change_pct=v)
            ms = [calc_row(r, replace(resolve(r), volume_change_pct=v), a) for r, a in zip(rows, inside)]
        t = total(ms)
        cases.append({"volume_pct": v, "entered": v == entered, "units": t["units"], "net": t["net"],
                      "cm": t["cm"], "nm": t["nm"], "d_units": t["units"] - base["units"],
                      "d_cm": t["cm"] - base["cm"], "d_nm": t["nm"] - base["nm"]})
    return {"cases": cases}


def DIM_LABEL(dim, key):
    return data.LABEL[dim].get(key, key)


DIMS = data.DIM_IDS
BREAKDOWN_DIMS = ("chain", "state", "family", "sku")


def _group(items, dim):
    groups = {}
    for it in items:
        groups.setdefault(it["row"][dim], []).append(it)
    return groups


def _group_volume(grp):
    """Per group: the extra volume that would bring the group's contribution back to today's, at the new terms,
    against the volume change in the scenario. Same idea as breakeven(), for one chain, SKU, category or state."""
    base_cm = sum(g["base"]["cm"] for g in grp)
    fixed = sum(g["fixed_cm"] for g in grp)
    units_b = sum(g["base"]["units"] for g in grp)
    units_s = sum(g["sim"]["units"] for g in grp)
    assumed = units_s / units_b - 1 if units_b else 0.0
    out = {"status": "neutral", "required": None, "assumed": assumed}
    if not units_b or abs(fixed - base_cm) < NEUTRAL_RUPEES:
        return out
    if fixed <= 0:
        out["status"] = "unreachable"
        return out
    required = base_cm / fixed - 1
    out.update(status="ahead" if required <= 0 else "needs", required=required)
    return out


def breakdown(items, dim):
    out = []
    for key, grp in _group(items, dim).items():
        node = {"id": key, "label": DIM_LABEL(dim, key),
                "base": derive(total(g["base"] for g in grp)),
                "sim": derive(total(g["sim"] for g in grp))}
        if "fixed_cm" in grp[0]:
            node["volume"] = _group_volume(grp)
        out.append(node)
    out.sort(key=lambda n: n["sim"]["net"], reverse=True)
    return out


def _tree(items, dims, level, path):
    dim = dims[level]
    nodes = []
    for key, grp in _group(items, dim).items():
        node_id = f"{path}/{dim}:{key}"
        node = {"id": node_id, "dim": dim, "label": DIM_LABEL(dim, key),
                "base": derive(total(g["base"] for g in grp)),
                "sim": derive(total(g["sim"] for g in grp))}
        if level + 1 < len(dims):
            node["children"] = _tree(grp, dims, level + 1, node_id)
        nodes.append(node)
    nodes.sort(key=lambda n: n["sim"]["net"], reverse=True)
    return nodes


def _items(lv, scope, flt=None, sims=None, adjust=None):
    sims = sims if sims is not None else scenario_metrics(lv, scope, None, adjust)
    return [{"row": r, "base": b, "sim": s}
            for r, b, s in zip(data.ROWS, BASE_METRICS, sims)
            if flt is None or passes(r, flt)]


def simulate(levers_raw, scope_raw):
    """Everything on the simulator screen is for the selected rows only (all rows when nothing is selected).
    So the cost levers, which apply to every row in the view, change only the selection's numbers too."""
    lv, adjust = split_levers(levers_raw)
    scope = parse_scope(scope_raw)
    rows, base = view_rows(scope)
    sims = scenario_metrics(lv, scope, rows, adjust)
    resolve = make_resolver(lv, adjust)
    items = [{"row": r, "base": b, "sim": m, "fixed_cm": calc_row(r, resolve(r), True, fixed_volume=True)["cm"]}
             for r, b, m in zip(rows, base, sims)]
    return {
        "base": derive(total(base)),
        "sim": derive(total(sims)),
        "bridge": bridge(lv, scope, sims, rows, base, adjust),
        "breakeven": breakeven(lv, scope, sims, rows, base, adjust),
        "ladder": volume_ladder(lv, scope, sims, rows, base, adjust),
        "suggested_volume": suggested_volume_change(lv, scope, rows, adjust),
        "breakdown": {d: breakdown(items, d) for d in BREAKDOWN_DIMS},
        "rows_in_scope": len(rows),
        "rows_total": len(data.ROWS),
        "whole": derive(total(BASE_METRICS)),     # the whole business at baseline, to show the selection's share
    }


def pnl_items(levers_raw, scope_raw, filter_raw=None):
    """One entry per dataset row that passes the filters: {"row", "base", "sim"}. Used by the Excel export."""
    lv, adjust = split_levers(levers_raw)
    return _items(lv, parse_scope(scope_raw), parse_filter(filter_raw), adjust=adjust)


def pnl(levers_raw, scope_raw, dims, filter_raw=None):
    lv, adjust = split_levers(levers_raw)
    scope = parse_scope(scope_raw)
    flt = parse_filter(filter_raw)
    dims = [d for d in (dims or []) if d in DIMS]
    dims = list(dict.fromkeys(dims)) or ["chain", "state", "sku"]
    items = _items(lv, scope, flt, adjust=adjust)
    return {
        "dims": dims,
        "rows_shown": len(items),
        "rows_total": len(data.ROWS),
        "total": {"base": derive(total(i["base"] for i in items)),
                  "sim": derive(total(i["sim"] for i in items))},
        "tree": _tree(items, dims, 0, ""),
    }
