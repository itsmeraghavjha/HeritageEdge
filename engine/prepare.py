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
import math
import os
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

# The month (and optionally year) columns of the MIS sheet are found by name. If the names differ, set
# MIS_MONTH_COLUMN / MIS_YEAR_COLUMN in .env to the exact header text.
MONTH_HEADERS = {"month", "month name", "mth", "mnth", "period", "bill month", "billing month", "month year"}
YEAR_HEADERS = {"year", "fiscal year", "financial year", "fin year", "fy", "calendar year"}
_MONTH_WORDS = {}
for _i, (_abbr, _full) in enumerate(zip("jan feb mar apr may jun jul aug sep oct nov dec".split(),
                                        "january february march april may june july august september october "
                                        "november december".split()), 1):
    _MONTH_WORDS.update({_abbr: _i, _full: _i})
_MONTH_WORDS["sept"] = 9
UNKNOWN_YEAR = "????"

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


# ---------- MIS months ----------
def _norm_header(h):
    return re.sub(r"[\s_]+", " ", str(h).strip().lower())


def find_header(hdr, wanted, override, what):
    """The header text of the month or year column: the .env override if set, else the one header whose
    name is in `wanted`. Returns None if there is none; raises if the choice is ambiguous or the override is wrong."""
    names = [h for h in hdr if h]
    if override:
        hit = [h for h in names if _norm_header(h) == _norm_header(override)]
        if not hit:
            raise DataError(f"The MIS has no column called '{override}' (set in .env for the {what}). "
                            f"Columns found: {', '.join(names[:80])}.")
        return hit[0]
    hits = [h for h in names if _norm_header(h) in wanted]
    if len(hits) > 1:
        raise DataError(f"The MIS has more than one column that could hold the {what}: {', '.join(hits)}. "
                        f"Set MIS_{what.upper()}_COLUMN in .env to the one to use.")
    return hits[0] if hits else None


def parse_month(value):
    """(year or None, month number) from a MIS month cell, or None if it cannot be read.

    Reads real dates, 202607, '2026-07', 'Jul-26', "Jul'26", 'July 2026', and plain 'Jul' (month only)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.year, value.month
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = int(value)
        if value != n:
            return None
        if 190001 <= n <= 209912 and 1 <= n % 100 <= 12:
            return n // 100, n % 100
        return (None, n) if 1 <= n <= 12 else None
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?(?:[ T].*)?", s)
    if m:
        return (int(m[1]), int(m[2])) if 1 <= int(m[2]) <= 12 else None
    m = re.fullmatch(r"([A-Za-z]+)[\s'\-/.,]*(\d{2}|\d{4})", s)
    if m and m[1].lower() in _MONTH_WORDS:
        year = int(m[2])
        return (year + 2000 if year < 100 else year), _MONTH_WORDS[m[1].lower()]
    if s.lower() in _MONTH_WORDS:
        return None, _MONTH_WORDS[s.lower()]
    if re.fullmatch(r"\d{1,2}", s) and 1 <= int(s) <= 12:
        return None, int(s)
    return None


def parse_year(value):
    """('cal', year) for 2026, ('fy', start year) for '2026-27' or 'FY26-27' (April to March), else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("cal", int(value)) if value == int(value) and 1990 <= value <= 2100 else None
    s = str(value).strip()
    m = re.fullmatch(r"(?:FY\s*)?(\d{4})\s*[-/]\s*(\d{2}|\d{4})", s, re.I)
    if m:
        return "fy", int(m[1])
    m = re.fullmatch(r"FY\s*(\d{2})\s*[-/]\s*(\d{2})", s, re.I)
    if m:
        return "fy", 2000 + int(m[1])
    return ("cal", int(s)) if re.fullmatch(r"\d{4}", s) and 1990 <= int(s) <= 2100 else None


def month_key(month_value, year_value=None):
    """'YYYY-MM-01' for a MIS row, or '????-MM-01' when the MIS gives the month but no year. None if unreadable."""
    parsed = parse_month(month_value)
    if parsed is None:
        return None
    year, month = parsed
    if year is None and year_value is not None:
        py = parse_year(year_value)
        if py is None:
            return None
        year = py[1] if py[0] == "cal" else (py[1] if month >= 4 else py[1] + 1)
    return f"{year:04d}-{month:02d}-01" if year else f"{UNKNOWN_YEAR}-{month:02d}-01"


def month_text(key):
    if key.startswith(UNKNOWN_YEAR):
        return date(2000, int(key[5:7]), 1).strftime("%b")
    return date(int(key[:4]), int(key[5:7]), 1).strftime("%b %Y")


def pick_mis_months(mis, base_months):
    """Keeps only the MIS rows for the base months, so costs and billing describe the same period.

    Returns (rows, months the MIS has, whether the MIS says which year). Stops if a base month is missing."""
    have = sorted(mis["month"].unique())
    wanted = set(base_months) | {f"{UNKNOWN_YEAR}-{b[5:7]}-01" for b in base_months}
    missing = [b for b in base_months if b not in have and f"{UNKNOWN_YEAR}-{b[5:7]}-01" not in have]
    if missing:
        raise DataError("The MIS has no Modern Formats or E-Commerce rows for " + ", ".join(month_text(b) for b in missing)
                        + ". It has: " + (", ".join(month_text(k) for k in have) or "no readable months")
                        + ". Untick those months, or upload a newer MIS.")
    return mis[mis["month"].isin(wanted)].copy(), have, not any(k.startswith(UNKNOWN_YEAR) for k in have)


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
    month_col = find_header(hdr, MONTH_HEADERS, os.environ.get("MIS_MONTH_COLUMN"), "month column")
    year_col = find_header(hdr, YEAR_HEADERS, os.environ.get("MIS_YEAR_COLUMN"), "year column")
    if month_col is None:
        raise DataError("The MIS has no month column that the app can recognise, so it cannot use only the months "
                        "you chose. Set MIS_MONTH_COLUMN in .env to the header of the month column. Columns found: "
                        + ", ".join(h for h in hdr if h)[:1500] + ".")
    m = m[m["Customer Group"].isin(MIS_GROUPS)].copy()
    pairs = m[[month_col] + ([year_col] if year_col else [])].drop_duplicates()
    keys = {tuple(p): month_key(p[0], p[1] if year_col else None) for p in pairs.itertuples(index=False, name=None)}
    bad = [k for k, v in keys.items() if v is None]
    if bad:
        raise DataError(f"The month in the MIS column '{month_col}' could not be read. Examples: "
                        + "; ".join(" / ".join(str(x) for x in b) for b in bad[:5]) + ".")
    m["month"] = [keys[tuple(p)] for p in m[[month_col] + ([year_col] if year_col else [])].itertuples(index=False, name=None)]
    m = m.rename(columns=MIS_RENAME)
    m["sku"] = pd.to_numeric(m["sku"], errors="coerce")
    m["SalesOfficeID"] = pd.to_numeric(m["SalesOfficeID"], errors="coerce")
    for c in ["packs", "rev"] + LINES:
        m[c] = pd.to_numeric(m[c], errors="coerce").fillna(0.0)
    if m.empty:
        raise DataError("The MIS sheet has no Modern Formats or E-Commerce rows.")
    out = m[["month", "sku", "SalesOfficeID", "family", "grp", "packs", "rev"] + LINES]
    out.attrs.update(month_column=month_col, year_column=year_col)
    return out


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


def price_check(rows, mis):
    """Does the model's revenue per pack agree with the MIS's revenue per pack for the same SKU, sales office
    and customer group?

    COGS, S&D and over/under are taken from the MIS per pack, but revenue per pack comes from billing. If the
    two revenues differ, every per-pack cost looks smaller or larger as a share of revenue, and the margin
    shifts with it. `rows` needs sku, SalesOfficeID, grp, family, name, units and net_px.

    gap_pct is the model's revenue over the MIS's revenue for the same packs, minus 1: +3% means the model
    sells the same packs for 3% more. The bands show whether that gap is spread evenly or sits on a few lines."""
    keys = ["sku", "SalesOfficeID", "grp"]
    m = mis.groupby(keys)[["packs", "rev"]].sum().reset_index()
    m = m[(m["packs"] > 0) & (m["rev"] > 0)]
    m["mis_px"] = m["rev"] / m["packs"]
    j = rows.merge(m[keys + ["mis_px"]], on=keys, how="left")
    j["ours"] = j["units"] * j["net_px"]
    total = float(j["ours"].sum())
    j = j[j["mis_px"].notna()].copy()
    j["theirs"] = j["units"] * j["mis_px"]
    j["gap"] = j["ours"] / j["theirs"] - 1

    def summary(d):
        ours, theirs = float(d["ours"].sum()), float(d["theirs"].sum())
        return {"model_rev": ours, "mis_rev": theirs, "gap_pct": ours / theirs - 1 if theirs else None}

    def grouped(col):
        return [dict(name=str(k), **summary(d)) for k, d in j.groupby(col)]

    ours = j["ours"].sum()
    bands = {}
    for label, lo, hi in (("within_1pct", 0, 0.01), ("1_to_5pct", 0.01, 0.05), ("over_5pct", 0.05, float("inf"))):
        pick = (j["gap"].abs() >= lo) & (j["gap"].abs() < hi)
        bands[label] = float(j.loc[pick, "ours"].sum() / ours) if ours else 0.0
    # one row per SKU code: the same code can carry two spellings of its name in the offer file
    by_sku = j.groupby("sku").agg(name=("name", "first"), ours=("ours", "sum"), theirs=("theirs", "sum")).reset_index()
    by_sku["diff"] = by_sku["ours"] - by_sku["theirs"]
    by_sku["gap_pct"] = by_sku["ours"] / by_sku["theirs"] - 1
    top = by_sku.reindex(by_sku["diff"].abs().sort_values(ascending=False).index).head(10)
    return {"matched_revenue_pct": float(ours / total) if total else 0.0, "overall": summary(j) if len(j) else None,
            "by_group": grouped("grp"), "by_family": grouped("family"), "revenue_by_gap_size": bands,
            "largest_gaps": [{"sku": str(r.sku), "name": str(r.name), "gap_pct": float(r.gap_pct),
                              "diff_rev": float(r.diff)} for r in top.itertuples()]}


def mix_check(everyone, mis):
    """Is the revenue per pack gap about which customers are in the cell, or about how revenue is defined?

    The MIS price for a SKU x sales office x customer group cell averages every customer in the cell, but the
    model only holds customers that are in the offer file. If the offer-file customers pay more than the rest,
    the model looks higher than the MIS even when billing and the MIS agree. This compares the MIS with all
    billing in the same cells, then splits it into offer-file customers and the others.
    `everyone` needs sku, SalesOfficeID, grp, packs, rev and in_offer (True for offer-file customers)."""
    keys = ["sku", "SalesOfficeID", "grp"]
    m = mis.groupby(keys)[["packs", "rev"]].sum().reset_index()
    m = m[(m["packs"] > 0) & (m["rev"] > 0)]
    m["mis_px"] = m["rev"] / m["packs"]
    j = everyone.merge(m[keys + ["mis_px", "packs"]].rename(columns={"packs": "mis_packs"}), on=keys, how="inner")
    j["theirs"] = j["packs"] * j["mis_px"]
    cells = j.drop_duplicates(keys)
    billing_packs, mis_packs = float(j["packs"].sum()), float(cells["mis_packs"].sum())

    def summary(d):
        ours, theirs = float(d["rev"].sum()), float(d["theirs"].sum())
        return {"billing_rev": ours, "mis_rev": theirs, "gap_pct": ours / theirs - 1 if theirs else None}

    total = float(j["rev"].sum())
    others = j[~j["in_offer"]]
    return {"all_customers": summary(j) if len(j) else None,
            "offer_customers": summary(j[j["in_offer"]]) if j["in_offer"].any() else None,
            "other_customers": summary(others) if len(others) else None,
            "other_customers_share_pct": float(others["rev"].sum() / total) if total else 0.0,
            # If billing and the MIS count packs the same way, these match when the months match. A different
            # unit (cases, litres, pack equivalents) would show here as a ratio far from 1.
            "packs": {"billing": billing_packs, "mis": mis_packs,
                      "ratio": billing_packs / mis_packs if mis_packs else None}}


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
    in_offer = pd.MultiIndex.from_frame(b[["cid", "sku"]]).isin(pd.MultiIndex.from_frame(terms[["cid", "sku"]]))
    everyone = b.loc[b["grp"].astype(str).str.strip().isin(MIS_GROUPS), ["sku", "so_num", "grp", "packs", "rev"]].copy()
    everyone["in_offer"] = in_offer[b["grp"].astype(str).str.strip().isin(MIS_GROUPS).to_numpy()]
    everyone = everyone.rename(columns={"so_num": "SalesOfficeID"})
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
    clipped_pct = float(g.loc[far, "rev"].abs().sum() / g["rev"].sum()) if g["rev"].sum() else 0.0

    say("Reading the MIS workbook. This takes a few minutes", 0.2)
    mis_all = load_mis(mis_path, progress)
    mis_cols = dict(mis_all.attrs)
    mis, mis_have, year_known = pick_mis_months(mis_all, base_months)
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
    check = price_check(g.loc[final.index, ["sku", "SalesOfficeID", "grp", "family", "name", "units", "net_px"]], mis)
    check["mix"] = mix_check(everyone, mis)
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
        "mis_months": {"used": base_months, "available": mis_have, "year_known": year_known,
                       "month_column": mis_cols.get("month_column"), "year_column": mis_cols.get("year_column")},
        "reconciliation": rec,
        "price_check": check,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    say("Done", 1.0)
    return meta
