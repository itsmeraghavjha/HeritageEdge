"""Builds data/rows.csv (the dataset the app runs on) from three sources:

  billing   data/billing.csv       monthly volumes and revenue, pulled from SQL Server (engine/sql_source.py)
  offer     the offer file         list prices, trade margin and offers per customer x SKU
  MIS       the P&L MIS workbook   costs by SKU x sales office x customer group, attributed down to chains

Scope: customer x SKU lines that exist in the offer file. Baseline volume is the average monthly packs over the
chosen base months; commercial terms come from the offer file's latest snapshot.

Cost attribution (config/attribution.csv): "volume" gives every chain the same cost per pack in a cell;
"revenue" gives every chain the same share of its net revenue. This is an allocation, not a measured cost to serve.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import data

LINES = list(data.LINES)
LINE_LABELS = {"cogs": "COGS", "sd": "S&D / freight", "pa": "P&A", "interest": "Interest",
               "ou": "Over/under", "mktg": "Marketing"}
MIS_SHEET = "SAP_MIS_DATABASE"
MIS_GROUPS = ["Modern Formats", "E-Commerce"]
MIS_SKU_COLUMN = 19  # the SKU column has no usable header in the MIS workbook, so it is found by position
MIS_RENAME = {"Actual data SKU Qty": "packs", "Actual data Revenue": "rev", "Actual data COGS": "cogs",
              "Actual data S&D Costs": "sd", "Actual data P&A Cost": "pa", "Actual data Interest": "interest",
              "Actual data O/U Abs": "ou", "Mktg Cost": "mktg", "ProdHier01-1": "family",
              "Customer Group": "grp", "PCCODE": "SalesOfficeID"}

OFFER_REQUIRED = ["Customer", "Sku Code", "Cluster", "Material", "New MRP", "TOT Margin on Invoice",
                  "On Invoice offer", "Off Invoice Offer in Value", "GST%"]
BILLING_REQUIRED = ["BillMonth", "CustomerGroup", "CustomerID", "SalesOfficeID", "SalesOfficeName", "CITY",
                    "STATE", "MaterialCode", "ProductFamily", "Variant", "PackSize", "Qty_LtrKg", "Packs",
                    "InvoiceValue_exTax"]


class DataError(Exception):
    """A problem with an input file, in words a user can act on."""


def _say(progress):
    return progress or (lambda *a, **k: None)


def _need(df, cols, what):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataError(f"The {what} is missing these columns: {', '.join(missing)}. "
                        "Check that it is the right file and has not been renamed or reordered.")


# ---------- helpers ----------
def pack_size(s):
    """Pack size in litres or kg from text like '400 GMS', '1.5 LTR' or '700+700ML' (a 1.4 L pack)."""
    t = str(s).upper()
    for a in ("LITRES", "LITERS", "LITRE", "LITER", "LTR"):
        t = t.replace(a, "L")
    nums = re.findall(r"[\d.]+", t)
    if not nums:
        return None
    try:
        tot = sum(float(n) for n in nums)
    except ValueError:
        return None
    return tot / 1000 if ("ML" in t or "GM" in t) else tot


def read_basis():
    return dict(data.BASIS) or {r["line"]: r["basis"] for r in data._read(data.CONFIG_DIR / "attribution.csv")}


def billing_months(path):
    """Months present in the billing file, as 'YYYY-MM-01' strings."""
    df = pd.read_csv(path, usecols=["BillMonth"])
    return sorted(pd.to_datetime(df["BillMonth"]).dt.strftime("%Y-%m-01").unique())


def default_base_months(months, today=None, count=3):
    """The last `count` complete months. The current calendar month is still being billed, so it is skipped."""
    today = today or date.today()
    current = today.strftime("%Y-%m-01")
    complete = [m for m in months if m < current]
    return complete[-count:]


# ---------- readers ----------
def load_offer(path):
    path = Path(path)
    engine = "pyxlsb" if path.suffix.lower() == ".xlsb" else None
    try:
        off = pd.read_excel(path, engine=engine)
    except ImportError:
        raise DataError("Reading .xlsb files needs the pyxlsb package. Run: pip install pyxlsb")
    except Exception as exc:
        raise DataError(f"The offer file could not be read: {exc}")
    off.columns = [str(c).strip() for c in off.columns]
    _need(off, OFFER_REQUIRED, "offer file")
    return off


def load_mis(path, progress=None):
    from openpyxl import load_workbook
    say = _say(progress)
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise DataError(f"The MIS workbook could not be opened: {exc}")
    if MIS_SHEET not in wb.sheetnames:
        raise DataError(f"The MIS workbook has no sheet called {MIS_SHEET}. Sheets found: {', '.join(wb.sheetnames)}.")
    rows, hdr = [], None
    for i, r in enumerate(wb[MIS_SHEET].iter_rows(values_only=True)):
        if i == 0:
            hdr = [str(h).strip() if h is not None else "" for h in r]
            if len(hdr) <= MIS_SKU_COLUMN:
                raise DataError("The MIS sheet has fewer columns than expected, so the SKU column cannot be found.")
            hdr[MIS_SKU_COLUMN] = "sku"
            continue
        if r[0] is None and r[1] is None:
            break
        rows.append(r)
        if i % 25000 == 0:
            say(f"Reading the MIS workbook: {i:,} rows", None)
    wb.close()
    m = pd.DataFrame(rows, columns=hdr)
    _need(m, list(MIS_RENAME), "MIS sheet")
    m = m[m["Customer Group"].isin(MIS_GROUPS)].rename(columns=MIS_RENAME)
    m["sku"] = pd.to_numeric(m["sku"], errors="coerce")
    m["SalesOfficeID"] = pd.to_numeric(m["SalesOfficeID"], errors="coerce")
    for c in ["packs", "rev"] + LINES:
        m[c] = pd.to_numeric(m[c], errors="coerce").fillna(0.0)
    if m.empty:
        raise DataError("The MIS sheet has no Modern Formats or E-Commerce rows.")
    return m[["sku", "SalesOfficeID", "family", "grp", "packs", "rev"] + LINES]


# ---------- cost attribution ----------
def rates(m, keys, basis):
    """MIS cost per pack (volume basis) or per rupee of revenue (revenue basis) at the given level."""
    t = m.groupby(keys)[["packs", "rev"] + LINES].sum().reset_index()
    out = t[keys].copy()
    for line in LINES:
        den = t["packs"] if basis[line] == "volume" else t["rev"]
        out[line] = (t[line] / den).where(den > 0)
    out["ok"] = (t["packs"] > 0) & (t["rev"] > 0)
    return out


def attribute_costs(g, mis, basis):
    """Adds the six cost columns to g (SKU x office x group level). Returns the cost-source shares."""
    cell = rates(mis, ["sku", "SalesOfficeID", "grp"], basis)
    skul = rates(mis, ["sku", "grp"], basis)
    famr = rates(mis.assign(packs=mis["rev"]), ["family", "grp"], {x: "revenue" for x in LINES})
    famr = famr.rename(columns={"family": "family_k"})
    for name, df, keys in (("c", cell, ["sku", "SalesOfficeID", "grp"]), ("s", skul, ["sku", "grp"])):
        g = g.merge(df.rename(columns={x: f"{x}_{name}" for x in LINES + ["ok"]}), on=keys, how="left")
    g["family_k"] = g["family"]
    g = g.merge(famr.rename(columns={x: f"{x}_f" for x in LINES}).drop(columns="ok"),
                on=["family_k", "grp"], how="left")
    src = pd.Series("family ratio", index=g.index)
    for line in LINES:
        fam = g[f"{line}_f"].fillna(0)
        v = (fam * g["net_px"] if basis[line] == "volume" else fam).copy()   # family fallback: % of revenue
        use_s = g["ok_s"].fillna(False).astype(bool) & g[f"{line}_s"].notna()
        v[use_s] = g.loc[use_s, f"{line}_s"]
        use_c = g["ok_c"].fillna(False).astype(bool) & g[f"{line}_c"].notna()
        v[use_c] = g.loc[use_c, f"{line}_c"]
        g[line] = v.clip(-0.5 * g["net_px"], 3 * g["net_px"]) if basis[line] == "volume" else v.clip(-0.5, 2.0)
        if line == "cogs":
            src[use_s] = "sku average"
            src[use_c] = "sku x office"
    return g, src.value_counts(normalize=True).round(3).to_dict()


def reconcile(rows, mis, basis):
    """Compares each attributed cost, as a share of net revenue, with the same share in the MIS itself.

    The MIS covers more customers and months than the offer-file scope, so some gap is expected.
    A large gap on one line shows where the attribution needs attention."""
    out = []
    for grp in ["All"] + MIS_GROUPS:
        r = rows if grp == "All" else rows[rows["grp"] == grp]
        m = mis if grp == "All" else mis[mis["grp"] == grp]
        rev_model = float((r["units"] * r["net_px"]).sum())
        rev_mis = float(m["rev"].sum())
        if rev_model <= 0 or rev_mis <= 0:
            continue
        lines, model_sum, mis_sum = [], 0.0, 0.0
        for line in LINES:
            cost = r["units"] * r[line] if basis[line] == "volume" else r["units"] * r["net_px"] * r[line]
            mp, sp = float(cost.sum()) / rev_model, float(m[line].sum()) / rev_mis
            model_sum += mp
            mis_sum += sp
            lines.append({"line": line, "label": LINE_LABELS[line], "model_pct": mp, "mis_pct": sp})
        out.append({"group": grp, "lines": lines, "model_margin_pct": 1 - model_sum, "mis_margin_pct": 1 - mis_sum})
    return out


# ---------- the build ----------
def build_dataset(offer_path, mis_path, billing_path, base_months, progress=None, out_dir=None):
    """Writes rows.csv and meta.json to out_dir (default: the data folder). Returns the meta dict."""
    say = _say(progress)
    out_dir = Path(out_dir or data.DATA_DIR)
    base_months = sorted(base_months)
    if not base_months:
        raise DataError("Choose at least one base month.")
    basis = read_basis()

    say("Reading the offer file", 0.05)
    off = load_offer(offer_path)
    off["cid"] = pd.to_numeric(off["Customer"], errors="coerce")
    off["sku"] = pd.to_numeric(off["Sku Code"], errors="coerce")
    off["chain"] = off["Cluster"].astype(str).str.strip().str.upper()
    off["offer_rs"] = off["On Invoice offer"].fillna(0) + off["Off Invoice Offer in Value"].fillna(0)
    terms = off.groupby(["cid", "sku"]).agg(
        chain=("chain", "first"), name=("Material", "first"), mrp=("New MRP", "mean"),
        trade=("TOT Margin on Invoice", "mean"), offer_rs=("offer_rs", "mean"), gst=("GST%", "mean")).reset_index()
    complete = terms[["mrp", "trade", "gst"]].notna().all(axis=1) & (terms["mrp"] > 0)
    terms_dropped = int((~complete).sum())
    terms = terms[complete]

    say("Reading billing volumes", 0.12)
    try:
        b = pd.read_csv(billing_path, low_memory=False)
    except FileNotFoundError:
        raise DataError("There are no billing volumes yet. Refresh from SQL first.")
    _need(b, BILLING_REQUIRED, "billing file")
    b["BillMonth"] = pd.to_datetime(b["BillMonth"]).dt.strftime("%Y-%m-01")
    absent = [m for m in base_months if m not in set(b["BillMonth"])]
    if absent:
        raise DataError("Billing has no data for: " + ", ".join(absent) + ". Refresh from SQL or choose other months.")
    b = b[b["BillMonth"].isin(base_months)].copy()
    b["rev"] = pd.to_numeric(b["InvoiceValue_exTax"], errors="coerce").fillna(0.0)
    total_rev, mf_rev = b["rev"].sum(), b.loc[b["CustomerGroup"].isin(MIS_GROUPS), "rev"].sum()

    # packs: use the SQL value; where SQL could not read the pack size, try again here
    packs = pd.to_numeric(b["Packs"], errors="coerce")
    size = b["PackSize"].map(pack_size)
    redo = packs.isna() & (size > 0)
    packs[redo] = pd.to_numeric(b["Qty_LtrKg"], errors="coerce")[redo] / size[redo]
    b["packs"] = packs
    unreadable = b["packs"].isna() | (b["packs"] <= 0)
    unreadable_pct = float(b.loc[unreadable, "rev"].sum() / total_rev) if total_rev else 0.0
    b = b[~unreadable].copy()

    b = b.rename(columns={"CustomerID": "cid", "MaterialCode": "sku", "CustomerGroup": "grp", "STATE": "state",
                          "CITY": "city", "SalesOfficeName": "so_name", "ProductFamily": "family",
                          "Variant": "variant", "PackSize": "pack"})
    b["cid"] = pd.to_numeric(b["cid"], errors="coerce")
    b["sku"] = pd.to_numeric(b["sku"], errors="coerce")
    b["so_num"] = pd.to_numeric(b["SalesOfficeID"], errors="coerce")
    b = b.merge(terms, on=["cid", "sku"], how="inner")      # offer-file scope only
    if b.empty:
        raise DataError("No billing lines match the offer file. Check that both cover the same customers and SKUs.")
    in_scope_pct = float(b["rev"].sum() / total_rev) if total_rev else 0.0
    in_scope_mf_pct = float(b.loc[b["grp"].isin(MIS_GROUPS), "rev"].sum() / mf_rev) if mf_rev else 0.0
    # The MIS only covers Modern Formats and E-Commerce. A line from any other group would get no costs and look
    # far more profitable than it is, so leave those lines out and say how much revenue that was.
    outside = ~b["grp"].astype(str).str.strip().isin(MIS_GROUPS)
    outside_pct = float(b.loc[outside, "rev"].sum() / total_rev) if total_rev else 0.0
    b = b[~outside].copy()
    if b.empty:
        raise DataError("Every billing line left after matching the offer file belongs to a customer group the MIS "
                        "does not cover. The MIS must include Modern Formats or E-Commerce.")

    n = len(base_months)
    for k in ("mrp", "trade", "offer_rs", "gst"):
        b["w_" + k] = b[k] * b["packs"]
    keys = ["chain", "grp", "state", "city", "so_num", "so_name", "sku", "name", "family", "variant", "pack"]
    for k in ("state", "city", "so_name", "family", "variant", "pack", "grp"):
        b[k] = b[k].fillna("").astype(str).str.strip()
    g = b.groupby(keys, as_index=False).agg(
        packs=("packs", "sum"), rev=("rev", "sum"), w_mrp=("w_mrp", "sum"), w_trade=("w_trade", "sum"),
        w_offer=("w_offer_rs", "sum"), w_gst=("w_gst", "sum"))
    g = g[g["packs"] > 0].copy()
    g["units"] = (g["packs"] / n).round(1)
    g["net_px"] = g["rev"] / g["packs"]
    g["mrp"], g["trade"] = g["w_mrp"] / g["packs"], g["w_trade"] / g["packs"]
    g["offer_rs"], g["gst"] = g["w_offer"] / g["packs"], g["w_gst"] / g["packs"]
    g["mrp_x"] = g["mrp"] / (1 + g["gst"])
    gross_u = g["mrp_x"] * (1 - g["trade"])
    raw_promo = g["offer_rs"] / (g["mrp"] * (1 - g["trade"]))
    g["promo_rate"] = raw_promo.clip(0, 0.6)
    # everything else between list invoice and what was actually billed (credit notes, mix, other schemes)
    raw_other = 1 - g["promo_rate"] - g["net_px"] / gross_u
    g["other_rate"] = raw_other.clip(-0.5, 0.9)
    # A billed price far from the list price usually means a unit or price problem in the data, not a real discount.
    far = (raw_promo < 0) | (raw_promo > 0.6) | (raw_other < -0.5) | (raw_other > 0.9)
    clipped_pct = float(g.loc[far, "rev"].sum() / g["rev"].sum()) if g["rev"].sum() else 0.0

    say("Reading the MIS workbook. This takes a few minutes", 0.2)
    mis = load_mis(mis_path, progress)
    say("Attributing MIS costs to chains", 0.8)
    g["SalesOfficeID"] = g["so_num"]
    g, cost_source = attribute_costs(g, mis, basis)

    out = g.assign(so=g["SalesOfficeID"].astype("Int64").astype(str), sku=g["sku"].astype("int64").astype(str))
    out["state"] = out["state"].str.title()
    cols = ["chain", "grp", "state", "city", "so", "so_name", "sku", "name", "family", "variant", "pack",
            "units", "mrp_x", "trade", "promo_rate", "other_rate", "net_px"] + LINES
    final = out[cols].round(5)
    numeric = ["units", "mrp_x", "trade", "promo_rate", "other_rate", "net_px"] + LINES
    valid = final[numeric].replace([float("inf"), float("-inf")], float("nan")).notna().all(axis=1)
    rows_invalid = int((~valid).sum())
    final = final[valid]
    out_dir.mkdir(parents=True, exist_ok=True)
    final.to_csv(out_dir / "rows.csv", index=False)

    rec = reconcile(final, mis, basis)
    meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "base_months": base_months,
        "rows": int(len(final)),
        "net_revenue_cr": round(float((final["units"] * final["net_px"]).sum() / 1e7), 2),
        "quality": {"billing_revenue_with_unreadable_pack_size_pct": round(unreadable_pct * 100, 2),
                    "billing_revenue_in_offer_scope_pct": round(in_scope_pct * 100, 1),
                    "mf_ecom_revenue_in_offer_scope_pct": round(in_scope_mf_pct * 100, 1),
                    "billing_revenue_outside_mis_groups_pct": round(outside_pct * 100, 2),
                    "revenue_with_price_gap_clipped_pct": round(clipped_pct * 100, 2),
                    "offer_lines_dropped_missing_terms": terms_dropped,
                    "rows_dropped_invalid_numbers": rows_invalid,
                    "cogs_source_share": cost_source},
        "reconciliation": rec,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    say("Done", 1.0)
    return meta
