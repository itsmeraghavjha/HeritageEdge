"""Where does "other deductions" come from?

Net revenue follows billing, so whatever the offer file does not explain lands in "other deductions". This module
takes the gap between billed revenue and the price the offer file implies and splits it into causes we can test, in
this order, so the pieces add up to the whole gap:

1. GST rate. Billing carries the tax it charged. If its rate differs from the offer file's GST%, the offer file's price
   was converted to ex-GST with the wrong rate.
2. Several offer rows. A customer x SKU with more than one row in the offer file is averaged by the build. The piece is
   what would go away if the row that best matches billing had been the one in force (an upper bound).
3. A price far from the offer file (more than 40% either way): usually a unit, pack-size or customer-mapping problem.
4. Moves between months: the gap left differs between the base months by more than half a percent, so terms changed
   during the period (the offer file holds one set of terms).
5. Steady gap: the same every month. A scheme, credit note or free goods that the offer file does not hold.

Shares are of billed revenue. "Net" adds up to the total gap; "absolute" ignores sign, so gaps that cancel still show.
"""
import numpy as np
import pandas as pd

FAR = 0.40          # a billed price this far from the offer file's is treated as a data problem
MOVE = 0.005        # a residual gap that changes by more than this between months means terms moved


def _price(mrp, trade, offer, gst):
    return (mrp * (1 - trade) - offer) / (1 + gst)


def explain(lines, offer_rows, months):
    """`lines`: billing lines in scope with cid, sku, BillMonth, packs, rev, mrp, trade, offer_rs, gst, chain, name
    (and Tax when billing has it). `offer_rows`: the raw offer rows with cid, sku, mrp, trade, offer_rs, gst.
    `months`: number of base months, to give rupees a month. Returns a dict, or None when nothing can be compared."""
    d = lines[(lines["packs"] > 0) & (lines["rev"] > 0)].copy()
    d["exp0"] = _price(d["mrp"], d["trade"], d["offer_rs"], d["gst"]) * d["packs"]
    d = d[d["exp0"] > 0]
    if d.empty:
        return None
    total_rev = float(d["rev"].sum())

    # 1. GST: only lines where billing really shows tax
    has_tax = "Tax" in d.columns
    tax = pd.to_numeric(d["Tax"], errors="coerce") if has_tax else pd.Series(np.nan, index=d.index)
    gst_b = (tax / d["rev"]).where(tax > 0).clip(0, 0.3)
    use_b = gst_b.notna()
    d["gst_use"] = np.where(use_b, gst_b.round(3), d["gst"])
    d["exp1"] = _price(d["mrp"], d["trade"], d["offer_rs"], d["gst_use"]) * d["packs"]
    gst_differs = use_b & ((gst_b - d["gst"]).abs() > 0.005)

    # 2. several offer rows: the best match for billing, per line
    d["px"] = d["rev"] / d["packs"]
    d["exp2"] = d["exp1"]
    multi_keys = set()
    if offer_rows is not None and len(offer_rows):
        o = offer_rows.dropna(subset=["mrp", "trade", "gst"]).copy()
        o["offer_rs"] = o["offer_rs"].fillna(0)
        cnt = o.groupby(["cid", "sku"]).size()
        o = o.merge(cnt.rename("n").reset_index(), on=["cid", "sku"])
        o = o[o["n"] > 1]
        if len(o):
            m = d.reset_index().merge(o[["cid", "sku", "mrp", "trade", "offer_rs", "gst"]],
                                      on=["cid", "sku"], suffixes=("", "_alt"))
            m["alt_px"] = _price(m["mrp_alt"], m["trade_alt"], m["offer_rs_alt"], m["gst_use"])
            m["miss"] = (m["px"] - m["alt_px"]).abs()
            best = m.sort_values("miss").groupby("index").first()
            d.loc[best.index, "exp2"] = best["alt_px"] * best["packs"]
            multi_keys = set(map(tuple, best[["cid", "sku"]].drop_duplicates().to_numpy()))
            d["multi"] = d.set_index(["cid", "sku"]).index.isin(list(multi_keys))
    d["multi"] = d.get("multi", False)

    p_gst = d["exp1"] - d["exp0"]
    p_rows = d["exp2"] - d["exp1"]
    rest = d["rev"] - d["exp2"]
    far = (rest.abs() / d["exp2"]) > FAR
    p_far = rest.where(far, 0.0)
    left = rest.where(~far, 0.0)

    # 4 and 5: what is left, split by whether it moves between months
    d["left"], d["left_pct"] = left, left / d["exp2"]
    by = d.groupby(["cid", "sku", "BillMonth"]).agg(l=("left", "sum"), e=("exp2", "sum")).reset_index()
    by["pct"] = by["l"] / by["e"]
    spread = by.groupby(["cid", "sku"])["pct"].agg(lambda s: s.max() - s.min() if len(s) > 1 else 0.0)
    moving = d.set_index(["cid", "sku"]).index.map(lambda k: spread.get(k, 0.0)) > MOVE
    p_move = left.where(moving, 0.0)
    p_steady = left.where(~moving, 0.0)

    parts = [("gst", "GST rate in billing differs from the offer file", p_gst),
             ("offer_rows", "Several offer rows for one customer and SKU", p_rows),
             ("far", "Billed price far from the offer file (data problem?)", p_far),
             ("moves", "Gap that changes between months (terms moved)", p_move),
             ("steady", "Same gap every month (scheme, credit note or free goods not in the offer file)", p_steady)]
    items = []
    for key, label, s in parts:
        items.append({"key": key, "label": label, "net_pct": float(s.sum() / total_rev),
                      "abs_pct": float(s.abs().sum() / total_rev), "rupees_a_month": float(s.sum() / months)})
    gap = float((d["rev"] - d["exp0"]).sum())

    # the biggest remaining gaps by chain x SKU, with the likely cause
    cause = pd.Series("steady", index=d.index)
    cause[moving] = "moves"
    cause[far] = "far"
    top = (d.assign(rest=rest, cause=cause).groupby(["chain", "sku", "name"])
           .agg(rest=("rest", "sum"), exp=("exp2", "sum"), cause=("cause", lambda s: s.value_counts().index[0]))
           .reset_index())
    top["pct"] = top["rest"] / top["exp"]
    top = top.reindex(top["rest"].abs().sort_values(ascending=False).index).head(10)
    # per month for those lines: what billing charged per pack against what the offer file implies
    month = {}
    pm = d.groupby(["chain", "sku", "BillMonth"]).agg(rev=("rev", "sum"), packs=("packs", "sum"), exp=("exp2", "sum")).reset_index()
    for r in pm.itertuples():
        if r.packs > 0 and r.exp > 0:
            month.setdefault((r.chain, r.sku), []).append(
                {"month": str(r.BillMonth)[:7], "billed_px": float(r.rev / r.packs), "file_px": float(r.exp / r.packs),
                 "gap_pct": float(r.rev / r.exp - 1)})
    return {
        "billed_revenue_a_month": total_rev / months,
        "total_gap_pct": gap / total_rev,
        "total_gap_rupees_a_month": gap / months,
        "causes": items,
        "billing_has_tax": bool(has_tax and use_b.any()),
        "lines_with_other_gst_rate_pct": float(d.loc[gst_differs, "rev"].sum() / total_rev),
        "lines_with_several_offer_rows_pct": float(d.loc[d["multi"], "rev"].sum() / total_rev),
        "left_after_causes": [{"chain": str(r.chain), "sku": str(r.sku), "name": str(r.name), "gap_pct": float(r.pct),
                               "rupees_a_month": float(r.rest / months), "cause": r.cause,
                               "months": sorted(month.get((r.chain, r.sku), []), key=lambda m: m["month"])} for r in top.itertuples()],
    }
