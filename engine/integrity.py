"""Data checks: can the inputs be trusted before we read any margin off them?

Each check asks one plain question of the billing, the offer file or the MIS, using only data already loaded, and
answers pass, warn or fail with the evidence. Thresholds are in the grade() calls so they are easy to find.
A check that cannot run (a column is missing) says so; it never stops a build.

Shares are of Modern Formats and E-Commerce billing revenue (the part the model can use) unless a check says otherwise.
"""
import re

import numpy as np
import pandas as pd

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skipped"
_ORDER = {PASS: 0, SKIP: 0, WARN: 1, FAIL: 2}

VOLUME_UNITS = {"L", "LT", "LTR", "LTRS", "LIT", "LITRE", "LITER", "LITRES", "LITERS"}
MASS_UNITS = {"KG", "KGS", "KILO", "KILOGRAM", "KILOGRAMS"}
GST_RATES = np.array([0.0, 0.05, 0.12, 0.18, 0.28])
_NAME_PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(ML|GMS|GM|G|KG|LTR|LT|L)\b", re.I)
CHECKS = []


# ---------- small helpers ----------
def pct(x, digits=1):
    v = x * 100
    return f"{0.0 if abs(v) < 0.5 * 10 ** -digits else v:.{digits}f}%"


def lakh(v):
    return f"₹{v / 1e5:,.1f} L"


def grade(value, warn, fail):
    return FAIL if value >= fail else WARN if value >= warn else PASS


def worst(*statuses):
    return max(statuses, key=lambda s: _ORDER[s])


def pack_class(text):
    """'volume' for ML / L packs, 'weight' for GM / KG packs, else None."""
    t = str(text).upper()
    if "ML" in t or "LTR" in t or "LITRE" in t or re.search(r"\d\s*L\b", t):
        return "volume"
    if "GM" in t or "KG" in t or re.search(r"\d\s*G\b", t):
        return "weight"
    return None


def name_pack_size(name, size_fn):
    """Pack size in litres or kg written in a product name ('Test curd 400 g'), or None."""
    hits = _NAME_PACK.findall(str(name))
    if not hits:
        return None
    num, unit = hits[-1]
    unit = {"G": "GM", "GMS": "GM", "LTR": "L", "LT": "L"}.get(unit.upper(), unit.upper())
    return size_fn(f"{num} {unit}")


def check(cid, area, title, question):
    """Registers a check. The function returns (status, headline, evidence rows, what to do)."""
    def wrap(fn):
        def run(c):
            base = {"id": cid, "area": area, "title": title, "question": question}
            try:
                status, headline, evidence, fix = fn(c)
            except Exception as exc:        # one broken check must not hide the others
                return {**base, "status": SKIP, "headline": f"Could not run this check ({type(exc).__name__}).",
                        "evidence": [], "fix": ""}
            return {**base, "status": status, "headline": headline, "evidence": [list(e) for e in evidence], "fix": fix}
        CHECKS.append(run)
        return run
    return wrap


def _name(c, sku):
    return c["names"].get(sku, str(int(sku)) if pd.notna(sku) else "unknown")


def _pair_share(c, pairs):
    """Share of usable billing revenue on the customer x SKU pairs in `pairs` (columns cid, sku)."""
    dm = c["dm"]
    if dm.empty or pairs is None or pairs.empty:
        return 0.0
    keys = pd.MultiIndex.from_frame(pairs[["cid", "sku"]].drop_duplicates())
    hit = pd.MultiIndex.from_frame(dm[["cid", "sku"]]).isin(keys)
    return float(dm.loc[hit, "rev"].sum() / dm["rev"].sum())


# ---------- units ----------
@check("unit_of_sale", "Units", "Unit of sale matches the pack",
       "Is every line billed in litres for volume packs and in kg for weight packs?")
def _unit_of_sale(c):
    dm = c["dm"]
    if "SalesUnit" not in dm.columns:
        return SKIP, "Billing has no SalesUnit column, so units cannot be checked. Refresh from SQL.", [], ""
    tot = dm["rev"].sum()
    u = dm["SalesUnit"].fillna("").astype(str).str.strip().str.upper()
    cls = pd.Series(np.select([u.isin(VOLUME_UNITS), u.isin(MASS_UNITS)], ["volume", "weight"], "other"), index=dm.index)
    pc = dm["PackSize"].map(pack_class)
    other = cls == "other"
    mism = ~other & pc.notna() & (cls != pc)
    s_other, s_mism = float(dm.loc[other, "rev"].sum() / tot), float(dm.loc[mism, "rev"].sum() / tot)
    ev = [(f"Billed in unit '{k}'", pct(v / tot)) for k, v in dm.groupby(u)["rev"].sum().sort_values(ascending=False).head(6).items()]
    bad = dm[other | mism].assign(unit=u).groupby(["sku", "PackSize", "unit"])["rev"].sum().sort_values(ascending=False).head(8)
    ev += [(f"{_name(c, k[0])}: {k[1]} pack billed in {k[2]}", pct(v / tot)) for k, v in bad.items()]
    status = worst(FAIL if s_other > 0.001 else PASS, FAIL if s_mism >= 0.01 else WARN if s_mism > 0 else PASS)
    head = (f"{pct(s_other)} of revenue is billed in a unit that is not litres or kg (pieces or cases would make every "
            f"pack count wrong). {pct(s_mism)} is billed in kg for a litre pack or the other way round (a density slip "
            f"changes packs and price per pack by about 9%).")
    return status, head, ev, ("Fix the SQL so quantity is in litres or kg, or convert it with each product's density "
                              "before packs are computed.") if status != PASS else ""


@check("whole_packs", "Units", "Packs come out as whole numbers",
       "Does quantity divided by pack size give a whole number of packs?")
def _whole_packs(c):
    dm = c["dm"][c["dm"]["packs"] > 0]
    tot = dm["rev"].sum()
    frac = (dm["packs"] - dm["packs"].round()).abs()
    bad = frac > 0.05
    share = float(dm.loc[bad, "rev"].sum() / tot)
    by = dm.loc[bad].groupby("sku")["rev"].sum().sort_values(ascending=False).head(8)
    ev = [(_name(c, k), pct(v / tot)) for k, v in by.items()]
    head = (f"{pct(share)} of revenue sits on lines where packs are not a whole number. An invoice sells whole packs, so a "
            "fraction means the quantity is in a different unit from the pack text (or the pack text is wrong). "
            "Passing does not prove units are right: some slips land on whole numbers by chance.")
    return grade(share, 0.02, 0.10), head, ev, "Look at the unit of sale and pack text of the SKUs listed." if share >= 0.02 else ""


@check("pack_identity", "Units", "Pack size agrees in billing and the offer file",
       "Does the pack in the billing text match the pack in the product name in the offer file, one text per SKU?")
def _pack_identity(c):
    dm, size_fn = c["dm"], c["pack_size"]
    tot = dm["rev"].sum()
    texts = dm.groupby("sku")["PackSize"].agg(lambda s: sorted(set(s.astype(str).str.strip().str.upper())))
    several = texts[texts.map(len) > 1]
    bill_size = dm.groupby("sku")["PackSize"].first().map(size_fn)
    rows, checked = [], 0
    for sku, nm in c["names"].items():
        want = name_pack_size(nm, size_fn)
        have = bill_size.get(sku)
        if want is None or have is None or pd.isna(have) or have <= 0:
            continue
        checked += 1
        if abs(want / have - 1) > 0.01:
            rows.append((sku, nm, want, have))
    rev = dm.groupby("sku")["rev"].sum()
    bad_rev = float(sum(rev.get(s, 0.0) for s, *_ in rows) / tot)
    ev = [(f"{nm}", f"name says {w:g}, billing says {h:g}") for s, nm, w, h in sorted(rows, key=lambda r: -rev.get(r[0], 0))[:8]]
    ev += [(f"{_name(c, k)}: more than one pack text", " / ".join(v)) for k, v in several.head(5).items()]
    head = (f"{checked} SKUs have a pack size in the offer-file name to compare; {len(rows)} disagree with billing "
            f"({pct(bad_rev)} of revenue). {len(several)} SKU codes carry more than one pack text in billing.")
    status = worst(grade(bad_rev, 0.005, 0.05), WARN if len(several) else PASS)
    return status, head, ev, "Check the SKU code against the product master: a code reused for another pack, or a wrong pack text." if status != PASS else ""


@check("price_spread", "Units", "The same SKU is priced alike across chains",
       "Is any chain billed a price per pack far from what other chains pay for the same SKU?")
def _price_spread(c):
    g = c["g"]
    g = g[(g["packs"] > 0) & (g["rev"] > 0)].copy()
    mean = g.groupby("sku").apply(lambda d: d["rev"].sum() / d["packs"].sum())
    g["dev"] = g["net_px"] / g["sku"].map(mean) - 1
    far = g["dev"].abs() > 0.25
    share = float(g.loc[far, "rev"].sum() / g["rev"].sum())
    top = g[far].assign(abs_dev=g["dev"].abs()).sort_values("rev", ascending=False).head(8)
    ev = [(f"{r.chain}, {r.name}", f"{'+' if r.dev > 0 else '-'}{abs(r.dev) * 100:.0f}% against the SKU average") for r in top.itertuples()]
    head = (f"{pct(share)} of revenue is on a chain whose price per pack is more than 25% from the average for that SKU. "
            "Chains do get different terms, but a gap this large more often means a pack count or unit slip.")
    return grade(share, 0.02, 0.10), head, ev, "Check the pack size and quantity unit for these chain lines." if share >= 0.02 else ""


# ---------- coverage ----------
@check("waterfall", "Coverage", "Every rupee of billing is accounted for",
       "From total billing to the revenue in the model, where did each rupee go, and does the end tie?")
def _waterfall(c):
    s, n = c["stages"], c["n"]
    total = s["total"]
    steps = [("All billing in the base months", total, ""),
             ("Left out: pack size could not be read", -s["unreadable"], "check"),
             ("Left out: customer and SKU not in the offer file, or its terms incomplete", -s["not_in_offer"], "by design, see the offer file checks"),
             ("Left out: customer group the MIS does not cover", -s["outside_groups"], "by design"),
             ("Left out: no packs left after grouping, or invalid numbers", -s["end_drop"], "check"),
             ("Revenue in the model", s["modelled"], "")]
    ev = [(f"{lab}{' (' + note + ')' if note else ''}", f"{lakh(v / n)} a month, {pct(v / total)}") for lab, v, note in steps]
    tie = abs(s["end_drop"]) / s["in_mis_scope"] if s["in_mis_scope"] else 0.0
    unread = s["unreadable"] / total if total else 0.0
    status = worst(grade(tie, 0.001, 0.01), grade(unread, 0.005, 0.02))
    head = (f"{pct(s['modelled'] / total)} of billing is in the model. {pct(unread)} was left out because the pack size could "
            f"not be read, and {pct(tie, 2)} was lost between grouping and the final table (should be zero apart from rounding).")
    return status, head, ev, "Fix the pack text of the unread lines, or find why rows vanish after grouping." if status != PASS else ""


@check("unreadable_packs", "Coverage", "Lines left out for pack size, and why",
       "Which billing lines end up with no pack count, and is it the pack text or the quantity?")
def _unreadable_packs(c):
    r, size_fn = c["rawn"], c["pack_size"]
    bad = r["packs"].isna() | (r["packs"] <= 0)
    d = r[bad]
    tot = float(r["rev"].sum())
    if d.empty:
        return PASS, "Every billing line has a pack count.", [], ""
    size = d["PackSize"].map(size_fn)
    no_text = size.isna() | (size <= 0)
    text, qty = d[no_text], d[~no_text]
    ev = [("Pack text has no readable size", f"{len(text):,} lines, {pct(float(text['rev'].sum()) / tot, 2)} of revenue"),
          ("Pack text is fine but quantity is zero or negative",
           f"{len(qty):,} lines, net {pct(float(qty['rev'].sum()) / tot, 2)} of revenue")]
    # the net can hide large amounts of opposite sign, so show each side
    for label, m in (("  of which revenue above zero", qty["rev"] > 0), ("  of which revenue below zero", qty["rev"] < 0),
                     ("  of which revenue exactly zero", qty["rev"] == 0)):
        part = qty[m]
        if len(part):
            ev.append((label, f"{len(part):,} lines, {pct(float(part['rev'].sum()) / tot, 2)} of revenue"))
    if len(qty) and "Qty_LtrKg" in qty.columns:
        q = pd.to_numeric(qty["Qty_LtrKg"], errors="coerce")
        ev.append(("  quantity below zero / exactly zero / missing", f"{int((q < 0).sum())} / {int((q == 0).sum())} / {int(q.isna().sum())} lines"))
    top = text.groupby(["MaterialCode", "PackSize", "SalesUnit"], dropna=False)["rev"].sum().sort_values(key=abs, ascending=False).head(8)
    for (code, txt, unit), v in top.items():
        ev.append((f"SKU {code} {_name(c, pd.to_numeric(code, errors='coerce'))}: pack text '{txt}', sold in {unit}", pct(float(v) / tot, 2)))
    fam = qty.groupby("ProductFamily", dropna=False)["rev"].sum().sort_values(key=abs, ascending=False).head(5) if "ProductFamily" in qty.columns else []
    for k, v in (fam.items() if len(qty) else []):
        ev.append((f"No quantity, family {k}", pct(float(v) / tot, 2)))
    share = abs(float(d["rev"].sum())) / tot if tot else 0.0
    head = (f"{pct(share, 2)} of billing has no pack count and is left out. "
            f"{pct(float(text['rev'].sum()) / tot, 2)} is because the pack text has no readable size; "
            f"{pct(float(qty['rev'].sum()) / tot, 2)} (net) is lines with no usable quantity, which cannot be turned into packs. "
            "The net can hide large amounts of opposite sign, so the split is shown below.")
    fix = []
    if len(text):
        fix.append("Send the pack texts listed and I will add a rule for each, or correct them in the product master.")
    if len(qty):
        fix.append("Lines with no quantity are revenue with no packs (usually credit notes, returns or price adjustments, but check the "
                   "split). They need to be carried as an adjustment, not left out, if the gross amounts are not small.")
    return grade(share, 0.001, 0.01), head, ev, " ".join(fix) if share >= 0.001 else ""


@check("credits", "Coverage", "Returns and credit notes are not silently dropped",
       "Is there billing with no quantity, or negative billing, that the build leaves out?")
def _credits(c):
    r = c["rawn"]
    pos = r.loc[r["rev"] > 0, "rev"].sum()
    no_qty = r["packs"].isna() | (r["packs"] <= 0)
    neg_left = float(-r.loc[no_qty & (r["rev"] < 0), "rev"].sum())
    pos_left = float(r.loc[no_qty & (r["rev"] > 0), "rev"].sum())
    neg_kept = float(-r.loc[~no_qty & (r["rev"] < 0), "rev"].sum())
    ev = [("Negative revenue left out (returns, credit notes)", f"{lakh(neg_left / c['n'])} a month, {pct(neg_left / pos)}"),
          ("Positive revenue with no quantity left out", f"{lakh(pos_left / c['n'])} a month, {pct(pos_left / pos)}"),
          ("Negative revenue kept with a positive quantity", f"{lakh(neg_kept / c['n'])} a month, {pct(neg_kept / pos)}")]
    net = abs(pos_left - neg_left) / pos
    head = (f"Lines with no quantity are dropped with their revenue. Positive and negative left-out lines are {pct(pos_left / pos)} "
            f"and {pct(neg_left / pos)} of billing, so the net effect on revenue is {pct(net, 2)}.")
    status = worst(grade(net, 0.005, 0.02), grade(neg_kept / pos, 0.005, 0.02))
    return status, head, ev, "Keep returns in the SQL with their signed quantity, so revenue and packs both net down." if status != PASS else ""


@check("duplicates", "Coverage", "No billing line counted twice",
       "Does any customer x office x SKU x month appear more than once in the billing file?")
def _duplicates(c):
    r = c["rawn"]
    keys = [k for k in ["BillMonth", "CustomerGroup", "CustomerID", "SalesOfficeID", "MaterialCode", "PackSize", "SalesUnit"] if k in r.columns]
    dup = r.duplicated(keys, keep="first")
    share = float(r.loc[dup, "rev"].abs().sum() / r["rev"].abs().sum()) if len(r) else 0.0
    ev = [("Repeated lines", f"{int(dup.sum()):,}"), ("Revenue on repeated lines", pct(share, 2))]
    head = ("No line is repeated." if not dup.any() else
            f"{int(dup.sum()):,} lines repeat an earlier line's customer, office, SKU, pack and month ({pct(share, 2)} of revenue). "
            "The SQL groups these, so repeats suggest the file was pulled or appended twice.")
    return (FAIL if dup.any() else PASS), head, ev, "Refresh billing from SQL again; do not append pulls to one file." if dup.any() else ""


@check("months_complete", "Coverage", "Each base month is a full month",
       "Is any base month much smaller or larger than the others, or still being billed?")
def _months_complete(c):
    base, monthly, today = c["base_months"], c["monthly"], c["today"]
    vals = [float(monthly.get(m, 0.0)) for m in base]
    ev = [(m[:7], lakh(v / 1)) for m, v in zip(base, vals)]
    later = [m for m in monthly.index if m > base[-1]]
    if later:
        ev.append(("Later months in the file (ignored)", ", ".join(m[:7] for m in later)))
    cur = today.strftime("%Y-%m-01")
    partial = cur in base
    if len(vals) < 2:
        return (WARN if partial else PASS), "Only one base month, so months cannot be compared with each other.", ev, ""
    med = float(np.median(vals))
    dev = max(abs(v / med - 1) for v in vals) if med else 0.0
    head = (f"The biggest base month is {pct(dev, 0)} away from the middle month. "
            + ("The current month is a base month and is still being billed. " if partial else "")
            + "A partial month understates volume.")
    status = worst(grade(dev, 0.25, 0.50), WARN if partial else PASS)
    return status, head, ev, "Untick the odd month, or wait until it is fully billed." if status != PASS else ""


@check("customers_one_chain", "Coverage", "Each customer belongs to one chain and one group",
       "Does any customer ID map to two chains in the offer file, or two customer groups in billing?")
def _customers_one_chain(c):
    off, r = c["off"], c["rawn"]
    chains = off.dropna(subset=["cid"]).groupby("cid")["chain"].nunique()
    multi_chain = chains[chains > 1].index
    groups = r.groupby("cid")["grp"].nunique()
    multi_group = groups[groups > 1].index
    dm = c["dm"]
    s1 = float(dm.loc[dm["cid"].isin(multi_chain), "rev"].sum() / dm["rev"].sum())
    s2 = float(dm.loc[dm["cid"].isin(multi_group), "rev"].sum() / dm["rev"].sum())
    ev = [("Customers on more than one chain in the offer file", f"{len(multi_chain)} ({pct(s1)} of revenue)"),
          ("Customers in more than one customer group in billing", f"{len(multi_group)} ({pct(s2)} of revenue)")]
    head = (f"{len(multi_chain)} customer IDs carry more than one chain name in the offer file, and {len(multi_group)} "
            "sit in more than one customer group in billing.")
    return worst(grade(s1, 0.005, 0.05), grade(s2, 0.005, 0.05)), head, ev, "Make the cluster name consistent for each customer ID." if (len(multi_chain) or len(multi_group)) else ""


@check("missed_lines", "Coverage", "No billing on a covered customer is missing",
       "Is revenue billed to offer-file customers on SKUs the offer file does not list, and who is outside the file?")
def _missed_lines(c):
    dm, pairs, cids = c["dm"], c["offer_pairs"], c["offer_cids"]
    tot = dm["rev"].sum()
    has_c = dm["cid"].isin(cids)
    has_p = pd.MultiIndex.from_frame(dm[["cid", "sku"]]).isin(pd.MultiIndex.from_frame(pairs[["cid", "sku"]].drop_duplicates()))
    out_c = ~has_c
    hole = has_c & ~has_p
    s_out, s_hole = float(dm.loc[out_c, "rev"].sum() / tot), float(dm.loc[hole, "rev"].sum() / tot)
    ev = [("Customers not in the offer file (by design)", f"{dm.loc[out_c, 'cid'].nunique()} customers, {pct(s_out)} of revenue"),
          ("Offer-file customers, SKU not in the file", f"{pct(s_hole)} of revenue")]
    for k, v in dm.loc[hole].groupby("sku")["rev"].sum().sort_values(ascending=False).head(5).items():
        ev.append((f"  missing SKU: {_name(c, k)}", pct(v / tot)))
    for k, v in dm.loc[out_c].groupby("cid")["rev"].sum().sort_values(ascending=False).head(5).items():
        ev.append((f"  largest customer outside the file: ID {int(k)}", pct(v / tot)))
    head = (f"{pct(s_out)} of Modern Formats and E-Commerce revenue is on customers the offer file does not cover (they are "
            f"left out on purpose). {pct(s_hole)} is on covered customers but on SKUs the file lacks, which should be near zero.")
    return grade(s_hole, 0.02, 0.10), head, ev, "Add the missing customer x SKU rows to the offer file." if s_hole >= 0.02 else ""


@check("cell_coverage", "MIS match", "Billing and the MIS cover the same cells",
       "Is there billing in a SKU x office x group that the MIS lacks, or the other way round?")
def _cell_coverage(c):
    keys = ["sku", "SalesOfficeID", "grp"]
    m = c["mis"].groupby(keys)[["packs", "rev"]].sum().reset_index()
    e = c["everyone"].groupby(keys)[["packs", "rev"]].sum().reset_index()
    j = e.merge(m, on=keys, how="outer", suffixes=("_b", "_m"), indicator=True)
    b_tot, m_tot = j["rev_b"].sum(), j["rev_m"].sum()
    b_only = float(j.loc[j["_merge"] == "left_only", "rev_b"].sum() / b_tot)
    m_only = float(j.loc[j["_merge"] == "right_only", "rev_m"].sum() / m_tot)
    ev = [("Billing revenue in cells with no MIS row", pct(b_only)), ("MIS revenue in cells with no billing", pct(m_only))]
    top = j[j["_merge"] == "left_only"].sort_values("rev_b", ascending=False).head(5)
    ev += [(f"  billed, not in the MIS: {_name(c, r.sku)}, office {int(r.SalesOfficeID) if pd.notna(r.SalesOfficeID) else '?'}",
            pct(r.rev_b / b_tot)) for r in top.itertuples()]
    head = (f"{pct(b_only)} of billing sits in cells the MIS has no row for (those get costs from the SKU average or family "
            f"ratio), and {pct(m_only)} of MIS revenue sits in cells with no billing.")
    return worst(grade(b_only, 0.03, 0.15), grade(m_only, 0.05, 0.25)), head, ev, \
        "Check office codes and SKU codes match in both sources." if max(b_only, m_only) >= 0.03 else ""


@check("cost_source", "MIS match", "Costs come from the MIS cell, not a fallback",
       "How many lines took their cost from a SKU average or a product-family ratio instead of their own cell?")
def _cost_source(c):
    src = c["cost_source"] or {}
    fam = float(src.get("family ratio", 0.0))
    ev = [(k, pct(v)) for k, v in sorted(src.items(), key=lambda kv: -kv[1])]
    head = f"{pct(fam)} of lines use the family ratio, the weakest fallback (share of lines, not of revenue)."
    return grade(fam, 0.05, 0.25), head, ev, "Make sure the MIS has rows for those SKUs and offices." if fam >= 0.05 else ""


@check("mis_packs_by_sku", "MIS match", "Packs billed equal packs in the MIS, SKU by SKU",
       "For each SKU, do billing and the MIS count the same number of packs and the same revenue?")
def _mis_packs(c):
    keys = ["sku", "SalesOfficeID", "grp"]
    m = c["mis"].groupby(keys)[["packs", "rev"]].sum().reset_index()
    e = c["everyone"].groupby(keys)[["packs", "rev"]].sum().reset_index()
    j = e.merge(m, on=keys, suffixes=("_b", "_m"))
    j = j[(j["packs_m"] > 0) & (j["rev_m"] > 0)]
    by = j.groupby("sku")[["packs_b", "rev_b", "packs_m", "rev_m"]].sum()
    by["p"] = by["packs_b"] / by["packs_m"]
    by["r"] = by["rev_b"] / by["rev_m"]
    off = (by["p"] - 1).abs() > 0.10
    share = float(by.loc[off, "rev_b"].sum() / by["rev_b"].sum())
    overall = float(by["packs_b"].sum() / by["packs_m"].sum())

    def reading(row):
        if abs(row.p - 1) > 0.10 and abs(row.r - 1) < 0.05:
            return "same revenue, different pack count: unit or pack definition"
        if abs(row.p - row.r) < 0.05:
            return "packs and revenue move together: different volume (months or customers)"
        return "packs and revenue both differ"
    top = by[off].sort_values("rev_b", ascending=False).head(8)
    ev = [("All SKUs together: packs billed over packs in the MIS", pct(overall))]
    ev += [(_name(c, k), f"packs {pct(r.p, 0)}, revenue {pct(r.r, 0)}: {reading(r)}") for k, r in top.iterrows()]
    head = (f"{pct(share)} of revenue is on SKUs whose pack count differs from the MIS by more than 10%. "
            "Same revenue with a different pack count is the signature of a unit slip; both moving together is a volume difference.")
    return grade(share, 0.05, 0.20), head, ev, "For the SKUs listed, compare the unit of sale and pack text with how the MIS counts packs." if share >= 0.05 else ""


@check("months_match_mis", "MIS match", "Billing and the MIS cover the same months",
       "Is the ratio of billing revenue to MIS revenue steady from month to month?")
def _months_match_mis(c):
    r, mis, base = c["rawn"], c["mis"], c["base_months"]
    mf = r[r["grp"].isin(c["groups"])]
    ratios, ev = [], []
    for bm in base:
        b = float(mf.loc[mf["BillMonth"] == bm, "rev"].sum())
        mrev = float(mis.loc[mis["month"].isin([bm, f"????-{bm[5:7]}-01"]), "rev"].sum())
        if mrev > 0:
            ratios.append(b / mrev)
            ev.append((bm[:7], f"billing {lakh(b)}, MIS {lakh(mrev)}, ratio {pct(b / mrev)}"))
    if not ratios:
        return SKIP, "The MIS has no revenue for the base months.", [], ""
    level, spread = float(np.mean(ratios)), max(ratios) - min(ratios)
    head = (f"Billing is {pct(level, 0)} of the MIS on average, and the ratio moves {pct(spread)} between months. A steady ratio "
            "means a definition difference; a jump in one month means one source is partial or shifted.")
    status = worst(grade(spread, 0.05, 0.15), grade(abs(level - 1), 0.10, 0.30))
    return status, head, ev, "Check the month cut-off in the MIS against billing dates." if status != PASS else ""


# ---------- tax and offer file ----------
@check("tax_rates", "Tax and offer file", "Billing tax is a real GST rate",
       "Does the tax on each billing line come to 0, 5, 12, 18 or 28% of its value?")
def _tax_rates(c):
    dm = c["dm"]
    if "Tax" not in dm.columns:
        return SKIP, "Billing has no Tax column. Refresh from SQL.", [], ""
    tax = pd.to_numeric(dm["Tax"], errors="coerce")
    if not (tax.fillna(0) > 0).any():
        return SKIP, "Billing carries no tax (every line is zero), so the GST rate cannot be checked.", [], ""
    rate = tax / dm["rev"]
    dist = np.abs(rate.to_numpy()[:, None] - GST_RATES[None, :])
    near, idx = np.nan_to_num(dist.min(axis=1), nan=9.0), np.nan_to_num(dist.argmin(axis=1))
    bad = pd.Series(near > 0.006, index=dm.index)
    tot = dm["rev"].sum()
    s_bad = float(dm.loc[bad, "rev"].sum() / tot)
    ev = [(f"GST {GST_RATES[i] * 100:g}%", pct(float(dm.loc[(~bad) & (idx == i), "rev"].sum() / tot))) for i in range(len(GST_RATES))]
    ev.append(("Some other rate", pct(s_bad)))
    s_id = 0.0
    if "InvoiceValue_inclTax" in dm.columns:
        incl = pd.to_numeric(dm["InvoiceValue_inclTax"], errors="coerce")
        if (incl.fillna(0) > 0).any():
            s_id = float(dm.loc[(incl - (dm["rev"] + tax)).abs() > 0.01 * dm["rev"], "rev"].sum() / tot)
            ev.append(("Value with tax is not value plus tax", pct(s_id)))
    gb = c.get("gap")
    if gb:
        ev.append(("Billing's rate differs from the offer file's GST%", pct(gb["lines_with_other_gst_rate_pct"])))
    head = f"{pct(s_bad)} of revenue carries tax that is not a standard GST rate; {pct(s_id)} fails value plus tax equals value with tax."
    return worst(grade(s_bad, 0.01, 0.05), grade(s_id, 0.01, 0.05)), head, ev, "Check the tax column in the SQL (it may be tax on a different base)." if max(s_bad, s_id) >= 0.01 else ""


@check("offer_units", "Tax and offer file", "Offer-file terms are in the right units",
       "Are GST and trade margin fractions (0.05, not 5), and is every offer below the price?")
def _offer_units(c):
    o = c["off"].dropna(subset=["cid", "sku"]).copy()
    gst, trade = pd.to_numeric(o["GST%"], errors="coerce"), pd.to_numeric(o["TOT Margin on Invoice"], errors="coerce")
    mrp, offer = pd.to_numeric(o["New MRP"], errors="coerce"), pd.to_numeric(o["offer_rs"], errors="coerce")
    flags = [("GST written as a whole number (5, not 0.05)", gst > 1.0, True),
             ("GST that is not a standard rate", gst.notna() & (gst <= 1.0) & (np.abs(gst.to_numpy()[:, None] - GST_RATES[None, :]).min(axis=1) > 0.002), False),
             ("Trade margin above 100% (whole number)", trade > 1.0, True),
             ("Negative trade margin", trade < 0, False),
             ("Trade margin above 60%", (trade > 0.6) & (trade <= 1.0), False),
             ("MRP of zero or less", mrp <= 0, False),
             ("Negative offer", offer < 0, False),
             ("Offer at or above the MRP", (offer >= mrp) & (mrp > 0), False)]
    ev, hard, soft = [], False, 0.0
    for label, mask, is_hard in flags:
        n = int(mask.sum())
        share = _pair_share(c, o.loc[mask, ["cid", "sku"]]) if n else 0.0
        ev.append((label, f"{n} rows, {pct(share)} of revenue"))
        if n and is_hard:
            hard = True
        soft = max(soft, share)
    head = (f"{len(o):,} offer rows read. " + ("Some terms look like whole-number percentages, which would break every price built from them. " if hard
            else "No unit slips found in GST or trade margin. ") + f"The worst other flag touches {pct(soft)} of revenue.")
    return worst(FAIL if hard else PASS, grade(soft, 0.005, 0.05)), head, ev, "Correct the offer file column, then rebuild." if hard or soft >= 0.005 else ""


@check("offer_conflicts", "Tax and offer file", "One set of terms per customer and SKU",
       "Do customer x SKU pairs with several offer rows carry different terms?")
def _offer_conflicts(c):
    o = c["off"].dropna(subset=["cid", "sku"]).copy()
    o["m"], o["t"], o["g"], o["f"] = (pd.to_numeric(o["New MRP"], errors="coerce"), pd.to_numeric(o["TOT Margin on Invoice"], errors="coerce"),
                                      pd.to_numeric(o["GST%"], errors="coerce"), pd.to_numeric(o["offer_rs"], errors="coerce").fillna(0))
    g = o.groupby(["cid", "sku"]).agg(n=("m", "size"), m_lo=("m", "min"), m_hi=("m", "max"), t_lo=("t", "min"), t_hi=("t", "max"),
                                      g_lo=("g", "min"), g_hi=("g", "max"), f_lo=("f", "min"), f_hi=("f", "max")).reset_index()
    multi = g[g["n"] > 1]
    diff = multi[((multi["m_hi"] / multi["m_lo"] - 1) > 0.01) | ((multi["t_hi"] - multi["t_lo"]) > 0.005)
                 | ((multi["g_hi"] - multi["g_lo"]) > 0.001) | ((multi["f_hi"] - multi["f_lo"]) > 0.5)]
    s_multi, s_diff = _pair_share(c, multi), _pair_share(c, diff)
    ev = [("Pairs with more than one offer row", f"{len(multi):,} ({pct(s_multi)} of revenue)"),
          ("... of which the rows disagree", f"{len(diff):,} ({pct(s_diff)} of revenue)"),
          ("  MRP differs", f"{int(((multi['m_hi'] / multi['m_lo'] - 1) > 0.01).sum())} pairs"),
          ("  trade margin differs", f"{int(((multi['t_hi'] - multi['t_lo']) > 0.005).sum())} pairs"),
          ("  offer amount differs", f"{int(((multi['f_hi'] - multi['f_lo']) > 0.5).sum())} pairs")]
    head = (f"{pct(s_diff)} of revenue is on a customer and SKU whose offer rows disagree; the build averages them, so any of those "
            "rows could be the one in force. Gap check shows how much of the price gap this explains.")
    return grade(s_diff, 0.02, 0.10), head, ev, "Keep one row per customer and SKU for the period, or add a validity date." if s_diff >= 0.02 else ""


@check("mrp_by_sku", "Tax and offer file", "One MRP per SKU within a state",
       "Does the same SKU carry different MRPs for customers in the same state?")
def _mrp_by_sku(c):
    o = c["off"].dropna(subset=["cid", "sku"]).copy()
    o["m"] = pd.to_numeric(o["New MRP"], errors="coerce")
    o = o[o["m"] > 0]
    dm = c["dm"]
    o = o[o["cid"].isin(set(dm["cid"]))]        # customers with no billing cannot affect any number
    # the state each customer mostly bills in; MRPs differ by state for milk and curd, which is not an error
    if "STATE" in dm.columns and len(dm):
        st = dm.groupby(["cid", dm["STATE"].astype(str).str.strip().str.title()])["rev"].sum().reset_index()
        st = st.sort_values("rev").drop_duplicates("cid", keep="last").set_index("cid").iloc[:, 0]
        o["state"] = o["cid"].map(st).fillna("(no billing)")
    else:
        o["state"] = "(unknown)"

    def mixed_of(keys):
        by = o.groupby(keys)["m"].agg(["min", "max", "nunique"])
        return by[(by["max"] / by["min"] - 1) > 0.01]

    overall = mixed_of(["sku"])
    within = mixed_of(["sku", "state"])
    skus_within = within.index.get_level_values("sku").unique()
    tot = dm["rev"].sum()
    share = float(dm.loc[dm["sku"].isin(skus_within), "rev"].sum() / tot) if tot else 0.0
    rev = dm.groupby("sku")["rev"].sum()
    w = within.reset_index()
    w["rev"] = w["sku"].map(rev).fillna(0)
    ev = [("SKUs with different MRPs across states (normal)", f"{len(overall)} SKUs"),
          ("SKUs with different MRPs inside one state", f"{len(skus_within)} SKUs ({pct(share)} of revenue)")]
    for _, r in w.sort_values("rev", ascending=False).drop_duplicates("sku").head(8).iterrows():
        ev.append((f"{_name(c, r['sku'])} ({r['state']})", f"₹{r['min']:g} to ₹{r['max']:g} ({int(r['nunique'])} values)"))
    head = (f"{len(overall)} SKUs carry more than one MRP across the offer file, which is normal when states price differently. "
            f"{len(skus_within)} still differ inside one state ({pct(share)} of revenue): that is usually a price revision caught "
            "mid-file, and the build then averages the old and new MRP.")
    # Terms are taken per customer and SKU, so different MRPs for different customers do no harm by themselves; the
    # harm is inside one customer x SKU (the check above) and in which MRP billing follows (the MRP revision check).
    return min(grade(share, 0.02, 0.20), WARN, key=lambda x: _ORDER[x]), head, ev, \
        "Only matters if the MRP check below says billing does not follow the file." if share >= 0.02 else ""


@check("offer_months", "Tax and offer file", "The offer is read from the base months",
       "Is the offer taken from each base month's own column, or from the file's latest-month numbers?")
def _offer_months(c):
    r = c.get("offer_months")
    if not r:
        raise ValueError("no offer report")
    names = ", ".join(r["used"] or r["missing"])
    if r["missing"]:
        head = (f"The offer file has no month column for {', '.join(r['missing'])}, so the build used its latest-month offer numbers. "
                "If those months had different offers, every price built from them is off by the difference.")
        return WARN, head, [("Months asked for", names), ("Source", r["source"])], \
            "Add the missing month column to the offer file, or choose base months it covers."
    d = r["rows_differ_from_numeric_pct"] or 0.0
    ev = [("Months read", names), ("Offer rows", f"{r['rows']:,}"),
          ("Rows whose offer differs from the file's latest-month numbers", pct(d)),
          ("Average offer per pack, base months and latest month", f"Rs {r['avg_offer_month']:.2f} and Rs {r['avg_offer_numeric']:.2f}"),
          ("Cells that could not be read", str(r["unreadable_cells"]))]
    head = (f"The offer comes from the {names} columns. {pct(d)} of offer rows differ from the file's latest-month numbers, "
            "which is the error a build on an earlier month would have carried.")
    bad = r["unreadable_cells"] > 0.01 * r["rows"]
    return (WARN if bad else PASS), head, ev, "Check the month text in the offer file." if bad else ""


@check("off_invoice", "Tax and offer file", "Off-invoice offers and the billed price",
       "Does billing deduct off-invoice offers, or are they claimed later and left out of the billed price?")
def _off_invoice(c):
    g = c["g"]
    if "on_rs" not in g.columns:
        raise ValueError("no on/off split")
    j = g[(g["packs"] > 0) & (g["net_px"] > 0)].copy()
    base = j["mrp"] * (1 - j["trade"])
    j["both"] = (base - j["on_rs"] - j["off_rs"]) / (1 + j["gst"])
    j["on_only"] = (base - j["on_rs"]) / (1 + j["gst"])
    j["billed"] = j["net_px"] * j["packs"]
    tot = float(j["billed"].sum())

    def gap(d, col):
        e = float((d[col] * d["packs"]).sum())
        return float(d["billed"].sum() / e - 1) if e > 0 else 0.0

    groups = [("No offer", (j["on_rs"] <= 0) & (j["off_rs"] <= 0)), ("On-invoice offer only", (j["on_rs"] > 0) & (j["off_rs"] <= 0)),
              ("Off-invoice offer only", (j["on_rs"] <= 0) & (j["off_rs"] > 0)), ("Both kinds", (j["on_rs"] > 0) & (j["off_rs"] > 0))]
    ev = []
    for label, m in groups:
        d = j[m]
        if d.empty:
            continue
        ev.append((label, f"{pct(float(d['billed'].sum()) / tot)} of revenue; billed price {pct(gap(d, 'both'))} against the price if the offer is deducted, "
                          f"{pct(gap(d, 'on_only'))} if only on-invoice offers are"))
    off_only = j[groups[2][1] | groups[3][1]]
    share = float(off_only["billed"].sum() / tot) if tot else 0.0
    if off_only.empty:
        return PASS, "No line carries an off-invoice offer.", ev, ""
    g_both, g_on = gap(off_only, "both"), gap(off_only, "on_only")
    left_out = abs(g_on) + 0.01 < abs(g_both)
    head = (f"{pct(share)} of revenue carries an off-invoice offer. Billing sits {pct(g_both)} from the price with the offer deducted and "
            f"{pct(g_on)} from the price without it. ")
    if left_out and share >= 0.02:
        head += ("So billing does not deduct these offers: they are claimed later. The model counts them as promotion while net revenue "
                 "stays at the billed value, so 'other deductions' is lower by the same amount and the claim cost may be missing from the P&L.")
        return WARN, head, ev, "Find where the off-invoice claims are booked (credit notes, MIS promotion line) and make sure they are costed once."
    return PASS, head + "Billing agrees with the offer file on these lines.", ev, ""


def _priced(c):
    """Billing lines in scope with the price the offer file implies (new MRP, trade margin, that month's offer), ex-GST,
    per pack: e_used per pack and x_used for the line."""
    d = c["lines"]
    d = d[(d["packs"] > 0) & (d["rev"] > 0)].copy()
    d["e_used"] = (d["mrp"] * (1 - d["trade"]) - d["offer_rs"]) / (1 + d["gst"])
    d = d[d["e_used"] > 0].copy()
    d["x_used"] = d["e_used"] * d["packs"]
    return d


def _gap(d, col="x_used"):
    e = float(d[col].sum())
    return float(d["rev"].sum() / e - 1) if e > 0 else 0.0


@check("trade_gst", "Price against the offer file", "How trade margin and GST combine",
       "On lines with GST and no offer, does billing follow MRP less trade margin, then ex-GST, or margin taken off the GST-inclusive MRP?")
def _trade_gst(c):
    d = _priced(c)
    d = d[(d["on_rs"] <= 0) & (d["off_rs"] <= 0) & (d["gst"] > 0.01)].copy()
    if d.empty:
        return SKIP, "No line with GST and no offer to compare.", [], ""
    # A: (MRP x (1 - trade)) / (1 + GST), the file's own "Basic cost".  B: MRP / (1 + GST) - trade x MRP.
    d["x_b"] = d["mrp"] * (1 / (1 + d["gst"]) - d["trade"]) * d["packs"]
    d = d[d["x_b"] > 0]
    ga, gb = _gap(d, "x_used"), _gap(d, "x_b")
    fam = d.groupby("family").apply(lambda x: pd.Series({"rev": x["rev"].sum(), "a": _gap(x, "x_used"), "b": _gap(x, "x_b")}),
                                     include_groups=False).sort_values("rev", ascending=False)
    tot = float(fam["rev"].sum())
    a_ok = float(fam.loc[fam["a"].abs() <= 0.005, "rev"].sum() / tot)
    b_ok = float(fam.loc[fam["b"].abs() <= 0.005, "rev"].sum() / tot)
    ev = [("Lines with GST and no offer", f"{pct(float(d['rev'].sum()) / float(_priced(c)['rev'].sum()))} of revenue"),
          ("Billed against A: margin off the MRP, then ex-GST (the file's Basic cost)", pct(ga)),
          ("Billed against B: ex-GST MRP less margin on the GST-inclusive MRP", pct(gb)),
          ("Revenue in families within 0.5% of A", pct(a_ok)), ("Revenue in families within 0.5% of B", pct(b_ok))]
    for f, r in fam.iterrows():
        ev.append((f"Family: {f}", f"A {pct(r['a'])}, B {pct(r['b'])}"))
    b_better = b_ok > a_ok + 0.1
    head = (f"With GST and no offer, billing is {pct(ga)} against A and {pct(gb)} against B overall. A is exact (within 0.5%) for families with "
            f"{pct(a_ok)} of this revenue and B for {pct(b_ok)}. "
            + ("B fits more families, so the margin is probably taken off the GST-inclusive MRP." if b_better else
               "B only looks better overall because it adds about 1% to every family; A is the rule where billing is exact, so the gap is specific to the families above, not to GST."))
    return (WARN if b_better else PASS), head, ev, "If B is right, tell us and the build will use it for products with GST." if b_better else ""


def sku_gap_table(c):
    """Every SKU's gap against the offer file, biggest first: revenue and gap per month, gap % and the running share of
    all the gap (ignoring sign). Used by the check below and written to sku_gap.csv at each build."""
    d = _priced(c)
    n = max(int(c.get("n") or 1), 1)
    t = d.groupby("sku").agg(name=("name", "first"), family=("family", "first"), rev=("rev", "sum"), exp=("x_used", "sum")).reset_index()
    t["diff"] = t["rev"] - t["exp"]
    t = t.reindex(t["diff"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    total_abs = float(t["diff"].abs().sum()) or 1.0
    out = pd.DataFrame({"rank": t.index + 1, "sku": t["sku"], "name": t["name"], "family": t["family"],
                        "revenue_a_month": t["rev"] / n, "gap_pct": t["diff"] / t["exp"], "gap_rupees_a_month": t["diff"] / n,
                        "cumulative_share_of_gap": t["diff"].abs().cumsum() / total_abs})
    return out


@check("gap_concentration", "Price against the offer file", "Where the gap is concentrated",
       "Is the gap spread over every SKU, or sitting in a few?")
def _gap_concentration(c):
    t = sku_gap_table(c)
    n = max(int(c.get("n") or 1), 1)
    tot_rev = float(_priced(c)["rev"].sum())
    if t.empty or not tot_rev:
        return SKIP, "No lines to compare.", [], ""
    net = float(t["gap_rupees_a_month"].sum())
    neg, pos = float(t.loc[t["gap_rupees_a_month"] < 0, "gap_rupees_a_month"].sum()), float(t.loc[t["gap_rupees_a_month"] > 0, "gap_rupees_a_month"].sum())
    k50 = int((t["cumulative_share_of_gap"] < 0.5).sum()) + 1
    k80 = int((t["cumulative_share_of_gap"] < 0.8).sum()) + 1
    clean = t[t["gap_pct"].abs() <= 0.005]
    clean_rev = float(clean["revenue_a_month"].sum() * n / tot_rev)
    ev = [("SKUs compared", f"{len(t):,}"), ("Net gap a month", lakh(net)),
          ("Below the file's price, all SKUs together", lakh(neg)), ("Above the file's price, all SKUs together", lakh(pos)),
          ("SKUs that make up half of all the gap", f"{k50}"), ("SKUs that make up 80% of all the gap", f"{k80}"),
          ("SKUs within 0.5% of the file's price", f"{len(clean):,} SKUs, {pct(clean_rev)} of revenue")]
    for r in t.head(15).itertuples():
        ev.append((f"{r.rank}. {r.name} ({r.family})", f"{pct(r.gap_pct)}, {lakh(r.gap_rupees_a_month)} a month, running {pct(r.cumulative_share_of_gap)} of the gap"))
    head = (f"{k50} of {len(t):,} SKUs make up half of the gap and {k80} make up 80%. Below the file's price the SKUs add up to {lakh(neg)} a month "
            f"and above it to {lakh(pos)}, a net {lakh(net)}. {len(clean):,} SKUs ({pct(clean_rev)} of revenue) are within 0.5% of the file. "
            "Every SKU is listed in sku_gap.csv in the data folder.")
    return grade(abs(net * n) / tot_rev, 0.005, 0.03), head, ev, "Take the SKUs at the top of the list to whoever owns their prices." if abs(net * n) / tot_rev >= 0.005 else ""


@check("no_offer_gap", "Price against the offer file", "A price gap with no offer involved",
       "On lines with no offer at all, does billing charge the file's price (MRP less trade margin, ex-GST)?")
def _no_offer_gap(c):
    d = _priced(c)
    d = d[(d["on_rs"] <= 0) & (d["off_rs"] <= 0)]
    tot = float(_priced(c)["rev"].sum())
    if d.empty:
        return SKIP, "Every line carries an offer.", [], ""
    d = d.assign(line_gap=d["rev"] / d["x_used"] - 1)
    gap = _gap(d)
    ev = [("Lines with no offer", f"{pct(float(d['rev'].sum()) / tot)} of revenue"), ("Billed price against the file's price", pct(gap))]
    for lo, hi, label in ((0, 0.005, "within 0.5% of the file's price"), (0.005, 0.03, "0.5% to 3% away"), (0.03, 9, "more than 3% away")):
        m = (d["line_gap"].abs() >= lo) & (d["line_gap"].abs() < hi)
        ev.append((f"Revenue {label}", pct(float(d.loc[m, "rev"].sum() / d["rev"].sum()))))
    d = d.assign(gst=d["gst"].round(3))
    for label, col in (("GST rate", "gst"), ("Family", "family"), ("Chain", "chain")):
        grp = d.groupby(col).apply(lambda x: pd.Series({"rev": x["rev"].sum(), "gap": _gap(x)}), include_groups=False)
        for k, r in grp.sort_values("rev", ascending=False).head(5 if col != "gst" else 3).iterrows():
            name = f"{k:.0%}" if col == "gst" else str(k)
            ev.append((f"{label}: {name}", f"{pct(r['gap'])} ({pct(r['rev'] / float(d['rev'].sum()))} of these lines)"))
    n = max(int(c.get("n") or 1), 1)
    sk = d.groupby("sku").apply(lambda x: pd.Series({"name": x["name"].iloc[0], "rev": x["rev"].sum(), "gap": _gap(x),
                                                     "diff": x["rev"].sum() - x["x_used"].sum()}), include_groups=False)
    for _, r in sk.reindex(sk["diff"].abs().sort_values(ascending=False).index).head(8).iterrows():
        ev.append((f"SKU: {r['name']}", f"{pct(r['gap'])}, {lakh(r['diff'] / n)} a month"))
    flat = float(d.loc[d["line_gap"].abs() >= 0.005, "rev"].sum() / d["rev"].sum()) > 0.6
    head = (f"With no offer involved, billing is {pct(gap)} against the file's price. "
            + ("Most lines are off by a similar small amount, which looks like a standing deduction or a definition difference, not one chain or SKU."
               if flat and abs(gap) >= 0.005 else "Where the gap sits is in the rows below."))
    status = grade(abs(gap), 0.005, 0.03)
    return status, head, ev, ("Ask finance whether a standing deduction (cash discount, rate difference, turnover claim) is posted against invoices; "
                              "it belongs in the offer file or in 'other deductions' as a named line." if status != PASS else "")


@check("gap_by_month", "Price against the offer file", "The gap in each base month",
       "Is the price gap the same in every base month?")
def _gap_by_month(c):
    d = _priced(c)
    months = sorted(d["BillMonth"].unique())
    if len(months) < 2:
        return SKIP, "Only one base month is built, so month-to-month movement cannot be seen. Build two or more months to test it.", [], ""
    gaps = [(m, _gap(d[d["BillMonth"] == m])) for m in months]
    ev = [(str(m)[:7], f"{pct(g)} against the file") for m, g in gaps]
    spread = max(g for _, g in gaps) - min(g for _, g in gaps)
    head = (f"The gap runs from {pct(min(g for _, g in gaps))} to {pct(max(g for _, g in gaps))} across the base months. "
            "The file's MRP is the New MRP in every month, so a difference between months can also come from a price or terms "
            "change in one of them.")
    status = grade(spread, 0.005, 0.02)
    return status, head, ev, ("Check whether prices or terms changed between these months (a mid-month change shows up as a "
                              "different gap in each month)." if status != PASS else "")


@check("dropped_terms", "Tax and offer file", "No offer line dropped for missing terms",
       "Is any offer line left out because it has no MRP, trade margin or GST?")
def _dropped_terms(c):
    d = c["dropped_pairs"]
    share = _pair_share(c, d)
    ev = [("Offer lines dropped", f"{len(d):,}"), ("Billing revenue on those customers and SKUs", pct(share))]
    head = f"{len(d):,} offer lines have a missing MRP, trade margin or GST and are left out, taking {pct(share)} of revenue with them."
    return grade(share, 0.005, 0.03), head, ev, "Fill in the missing terms in the offer file." if share >= 0.005 else ""


# ---------- entry point ----------
def run(ctx):
    """`ctx` is built in prepare.build_dataset. Returns {'checks': [...], 'summary': {...}}."""
    r = ctx["raw"].copy()
    r["cid"] = pd.to_numeric(r["CustomerID"], errors="coerce")
    r["sku"] = pd.to_numeric(r["MaterialCode"], errors="coerce")
    r["grp"] = r["CustomerGroup"].astype(str).str.strip()
    r["rev"] = pd.to_numeric(r["rev"], errors="coerce").fillna(0.0)
    r["packs"] = pd.to_numeric(r["packs"], errors="coerce")
    ctx = dict(ctx, rawn=r)
    ctx["dm"] = r[(r["rev"] > 0) & r["grp"].isin(ctx["groups"])].copy()
    off = ctx["off"]
    pairs = off.dropna(subset=["cid", "sku"])[["cid", "sku"]].drop_duplicates()
    ctx["offer_pairs"], ctx["offer_cids"] = pairs, set(pairs["cid"])
    names = {}
    if ctx["names"] is not None:
        names.update(ctx["names"])
    for s, d in r.groupby("sku"):
        names.setdefault(s, f"{d['Variant'].iloc[0]} {d['PackSize'].iloc[0]}".strip() if "Variant" in d.columns else str(int(s)))
    ctx["names"] = names
    out = [chk(ctx) for chk in CHECKS]
    summary = {k: sum(1 for x in out if x["status"] == k) for k in (PASS, WARN, FAIL, SKIP)}
    return {"checks": out, "summary": summary}
