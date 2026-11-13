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

import numpy as np
import pandas as pd

from . import data, gapcheck, integrity

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
        times = re.search(r"(\d+(?:\.\d+)?)\s*[X*]\s*(\d+(?:\.\d+)?)", t)      # '6 X 200 ML' is six packs of 200 ml
        tot = float(times.group(1)) * float(times.group(2)) if times else sum(float(n) for n in nums)
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


def offer_price_check(g, tol=0.005):
    """Does billing charge the price the offer file implies?

    The offer file says what a chain should pay per pack: MRP less trade margin, less the offer, all converted to
    ex-GST. `g` has one line per chain x office x SKU with `packs`, `net_px` (billed price per pack), `mrp`, `trade`,
    `offer_rs`, `gst`, `chain` and `name`. gap_pct is billed over expected, minus 1: -3% means billing charged 3%
    less than the offer file says. A gap on a few chains usually means an offer that changed during the month, several
    offer rows for one customer and SKU, or a scheme the offer file does not hold. Net revenue is not affected, since
    it comes from billing. The gap is what ends up in "other deductions"."""
    j = g[g["packs"] > 0].copy()
    j["expected_px"] = (j["mrp"] * (1 - j["trade"]) - j["offer_rs"]) / (1 + j["gst"])
    j = j[j["expected_px"] > 0].copy()
    if j.empty:
        return None
    j["billed"] = j["net_px"] * j["packs"]
    j["expected"] = j["expected_px"] * j["packs"]
    j["diff"] = j["billed"] - j["expected"]
    j["gap"] = j["net_px"] / j["expected_px"] - 1
    total = float(j["billed"].sum())

    def share(pick):
        return float(j.loc[pick, "billed"].sum() / total) if total else 0.0

    bands = {"matches": share(j["gap"].abs() <= tol),
             "below_up_to_10pct": share((j["gap"] < -tol) & (j["gap"] >= -0.10)),
             "below_over_10pct": share(j["gap"] < -0.10),
             "above": share(j["gap"] > tol)}
    by = j.groupby(["chain", "sku"]).agg(name=("name", "first"), billed=("billed", "sum"),
                                         expected=("expected", "sum")).reset_index()
    by["diff"] = by["billed"] - by["expected"]
    by["gap_pct"] = by["billed"] / by["expected"] - 1
    top = by.reindex(by["diff"].abs().sort_values(ascending=False).index).head(10)
    return {"billed_rev": float(j["billed"].sum()), "expected_rev": float(j["expected"].sum()),
            "gap_pct": float(j["billed"].sum() / j["expected"].sum() - 1),
            "revenue_by_gap": bands,
            "largest_gaps": [{"chain": str(r.chain), "sku": str(r.sku), "name": str(r.name),
                              "gap_pct": float(r.gap_pct), "diff_rev": float(r.diff)} for r in top.itertuples()]}


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



# ---------- the offer for the base months ----------
_MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def month_offer_column(columns, key):
    """The offer file's column for a month, e.g. "Aug'26 - Month Offer" for "2026-08-01", or None."""
    year, month = int(key[:4]), int(key[5:7])
    want = f"{_MON[month - 1]}'{str(year)[2:]}".lower()
    for c in columns:
        t = str(c).replace("\u2019", "'").strip().lower()
        if t.startswith(want) and "offer" in t:
            return c
    return None


def monthly_offers(off, base_months):
    """Sets on_rs, off_rs and offer_rs (rupees per pack, GST-inclusive like the MRP) on the offer rows.

    The offer file's numeric columns ("On Invoice offer", "Off Invoice Offer in Value") describe only the latest
    month. Each month also has a text column: "NLC Rs. 16 On Invoice Promo" (the chain pays Rs 16 against list price
    MRP x (1 - trade margin), so the on-invoice offer is the difference) or "Rs. 5 Promo - Off Invoice claim" (a
    claim paid later), and blank for no offer. When every base month has its column, the offer is the average of those
    months. If any is missing, the numeric columns are used and the report says so."""
    mrp = pd.to_numeric(off["New MRP"], errors="coerce")
    trade = pd.to_numeric(off["TOT Margin on Invoice"], errors="coerce")
    num_on = pd.to_numeric(off["On Invoice offer"], errors="coerce").fillna(0.0)
    num_off = pd.to_numeric(off["Off Invoice Offer in Value"], errors="coerce").fillna(0.0)
    cols = {k: month_offer_column(off.columns, k) for k in base_months}
    missing = [k for k, c in cols.items() if c is None]
    report = {"source": "month columns", "used": [], "missing": missing, "unreadable_cells": 0,
              "rows": int(len(off)), "rows_differ_from_numeric_pct": None}
    if missing:
        off["on_rs"], off["off_rs"] = num_on, num_off
        for key in base_months:
            off[f"on_rs|{key}"], off[f"off_rs|{key}"] = num_on, num_off
        report["source"] = "numeric columns (latest month)"
    else:
        on_sum, off_sum = np.zeros(len(off)), np.zeros(len(off))
        for key, col in cols.items():
            t = off[col].astype("string").fillna("").str.strip()
            val = pd.to_numeric(t.str.extract(r"Rs\.?\s*([\d.]+)")[0], errors="coerce")
            is_nlc = t.str.contains("NLC", case=False)
            is_off = t.str.contains("off invoice", case=False) & ~is_nlc
            report["unreadable_cells"] += int(((t != "") & (is_nlc | is_off) & val.isna()).sum()
                                              + ((t != "") & ~is_nlc & ~is_off).sum())
            on_m = np.nan_to_num(np.where(is_nlc, np.maximum(mrp * (1 - trade) - val, 0.0), 0.0))
            off_m = np.nan_to_num(np.where(is_off, val, 0.0))
            off[f"on_rs|{key}"], off[f"off_rs|{key}"] = on_m, off_m      # each month keeps its own offer
            on_sum += on_m
            off_sum += off_m
            report["used"].append(key)
        off["on_rs"], off["off_rs"] = on_sum / len(cols), off_sum / len(cols)
        differ = ((off["on_rs"] + off["off_rs"]) - (num_on + num_off)).abs() > 0.5
        report["rows_differ_from_numeric_pct"] = float(differ.mean()) if len(off) else 0.0
        report["avg_offer_month"] = float((off["on_rs"] + off["off_rs"]).mean())
        report["avg_offer_numeric"] = float((num_on + num_off).mean())
    off["offer_rs"] = off["on_rs"] + off["off_rs"]
    return off, report



# ---------- the build ----------

def period_info(base_months, mtd_days):
    """Describes the period the dataset covers. `mtd_days` is the number of days of billing a single, unfinished base
    month holds (month to date); with it the figures are the actual month-to-date amounts, not a monthly run-rate."""
    if not mtd_days:
        return {"mtd": False, "days": None, "label": ""}
    import calendar
    d = datetime.strptime(base_months[0][:10], "%Y-%m-%d")
    mon = d.strftime("%b")
    return {"mtd": True, "days": int(mtd_days),
            "label": f"{month_text(base_months[0])} month to date, 1 to {int(mtd_days)} {mon}",
            "short": f"{mon} MTD"}


def check_period(base_months, cost_month, mtd_days):
    """Validates the month-to-date and cost-month choices. Returns (cost_month or None, mtd_days or None)."""
    import calendar
    if mtd_days not in (None, ""):
        try:
            mtd_days = int(mtd_days)
        except (TypeError, ValueError):
            raise DataError("Days of billing in the month must be a whole number.")
        if len(base_months) != 1:
            raise DataError("Month to date works with one base month. Tick only the month that is still being billed.")
        d = datetime.strptime(base_months[0][:10], "%Y-%m-%d")
        last = calendar.monthrange(d.year, d.month)[1]
        if not 1 <= mtd_days <= last:
            raise DataError(f"{month_text(base_months[0])} has {last} days. Enter between 1 and {last} days of billing.")
        if mtd_days == last:
            mtd_days = None             # the whole month: nothing to say about month to date
    else:
        mtd_days = None
    if cost_month:
        if not re.fullmatch(r"\d{4}-\d{2}-01", str(cost_month)):
            raise DataError("The cost month is not a month.")
        if [cost_month] == list(base_months):
            cost_month = None           # the same month: the usual build
    else:
        cost_month = None
    return cost_month, mtd_days


def build_dataset(offer_path, mis_path, billing_path, base_months, progress=None, out_dir=None,
                  cost_month=None, mtd_days=None):
    """Writes rows.csv and meta.json to out_dir (default: the data folder). Returns the meta dict.

    `cost_month` takes the MIS cost rates (per pack, or as a share of revenue) from that month instead of the billing
    months, for when the MIS of the billing month does not exist yet. `mtd_days` says the one base month is unfinished
    and holds that many days of billing, so the figures are actual month to date and LPD divides by those days."""
    say = _say(progress)
    out_dir = Path(out_dir or data.DATA_DIR)
    base_months = sorted(base_months)
    if not base_months:
        raise DataError("Choose at least one base month.")
    cost_month, mtd_days = check_period(base_months, cost_month, mtd_days)
    cost_months = [cost_month] if cost_month else base_months
    costs_other = bool(cost_month)
    period = period_info(base_months, mtd_days)
    basis = read_basis()

    say("Reading the offer file", 0.05)
    off = load_offer(offer_path)
    off["cid"] = pd.to_numeric(off["Customer"], errors="coerce")
    off["sku"] = pd.to_numeric(off["Sku Code"], errors="coerce")
    off["chain"] = off["Cluster"].astype(str).str.strip().str.upper()
    off, offer_months = monthly_offers(off, base_months)
    parts = []
    for key in base_months:         # the offer is taken month by month, so a change between months is not averaged away
        t = off.assign(on_rs=off[f"on_rs|{key}"], off_rs=off[f"off_rs|{key}"])
        t["offer_rs"] = t["on_rs"] + t["off_rs"]
        parts.append(t.groupby(["cid", "sku"]).agg(
            chain=("chain", "first"), name=("Material", "first"), mrp=("New MRP", "mean"),
            trade=("TOT Margin on Invoice", "mean"), offer_rs=("offer_rs", "mean"), gst=("GST%", "mean"),
            on_rs=("on_rs", "mean"), off_rs=("off_rs", "mean")).reset_index().assign(BillMonth=key))
    terms = pd.concat(parts, ignore_index=True)
    complete = terms[["mrp", "trade", "gst"]].notna().all(axis=1) & (terms["mrp"] > 0)
    terms_dropped = int((~complete[terms["BillMonth"] == base_months[0]]).sum())
    dropped_pairs = terms.loc[~complete, ["cid", "sku"]].drop_duplicates()
    sku_names = dict(zip(terms["sku"], terms["name"]))
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
    monthly = pd.to_numeric(b["InvoiceValue_exTax"], errors="coerce").fillna(0.0).groupby(b["BillMonth"]).sum()
    b = b[b["BillMonth"].isin(base_months)].copy()
    b["rev"] = pd.to_numeric(b["InvoiceValue_exTax"], errors="coerce").fillna(0.0)
    total_rev, mf_rev = b["rev"].sum(), b.loc[b["CustomerGroup"].isin(MIS_GROUPS), "rev"].sum()

    # packs: use the SQL value; where SQL could not read the pack size, try again here
    packs = pd.to_numeric(b["Packs"], errors="coerce")
    size = b["PackSize"].map(pack_size)
    # where the pack text has no size (blank in the product master), read it from the SKU's name in the offer file
    name_size = pd.to_numeric(b["MaterialCode"], errors="coerce").map(sku_names).map(lambda n: integrity.name_pack_size(n, pack_size))
    from_name = (size.isna() | (size <= 0)) & (name_size > 0)
    size = size.where(~from_name, name_size)
    from_name_pct = float(b.loc[from_name & (packs.isna() | (packs <= 0)), "rev"].sum() / b["rev"].sum()) if b["rev"].sum() else 0.0
    redo = packs.isna() & (size > 0)
    packs[redo] = pd.to_numeric(b["Qty_LtrKg"], errors="coerce")[redo] / size[redo]
    b["packs"] = packs
    raw = b.copy()          # every base-month billing line, before anything is left out (for the integrity checks)
    unreadable = b["packs"].isna() | (b["packs"] <= 0)
    unreadable_rev = float(b.loc[unreadable, "rev"].sum())
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
    b = b.merge(terms, on=["cid", "sku", "BillMonth"], how="inner")      # offer-file scope only
    if b.empty:
        raise DataError("No billing lines match the offer file. Check that both cover the same customers and SKUs.")
    in_offer_rev = float(b["rev"].sum())
    in_scope_pct = float(b["rev"].sum() / total_rev) if total_rev else 0.0
    in_scope_mf_pct = float(b.loc[b["grp"].isin(MIS_GROUPS), "rev"].sum() / mf_rev) if mf_rev else 0.0
    # The MIS only covers Modern Formats and E-Commerce. A line from any other group would get no costs and look
    # far more profitable than it is, so leave those lines out and say how much revenue that was.
    outside = ~b["grp"].astype(str).str.strip().isin(MIS_GROUPS)
    outside_pct = float(b.loc[outside, "rev"].sum() / total_rev) if total_rev else 0.0
    outside_rev = float(b.loc[outside, "rev"].sum())
    b = b[~outside].copy()
    if b.empty:
        raise DataError("Every billing line left after matching the offer file belongs to a customer group the MIS "
                        "does not cover. The MIS must include Modern Formats or E-Commerce.")

    n = len(base_months)
    offer_rows = off.rename(columns={"New MRP": "mrp", "TOT Margin on Invoice": "trade", "GST%": "gst"})[
        ["cid", "sku", "mrp", "trade", "offer_rs", "gst"]]
    try:
        gap_breakdown = gapcheck.explain(b, offer_rows, n)
    except Exception:       # a diagnostic must never stop a build
        gap_breakdown = None
    for k in ("mrp", "trade", "offer_rs", "gst", "on_rs", "off_rs"):
        b["w_" + k] = b[k] * b["packs"]
    keys = ["chain", "grp", "state", "city", "so_num", "so_name", "sku", "name", "family", "variant", "pack"]
    for k in ("state", "city", "so_name", "family", "variant", "pack", "grp"):
        b[k] = b[k].fillna("").astype(str).str.strip()
    g = b.groupby(keys, as_index=False).agg(
        packs=("packs", "sum"), rev=("rev", "sum"), w_mrp=("w_mrp", "sum"), w_trade=("w_trade", "sum"),
        w_offer=("w_offer_rs", "sum"), w_gst=("w_gst", "sum"), w_on=("w_on_rs", "sum"),
        w_offrs=("w_off_rs", "sum"))
    g = g[g["packs"] > 0].copy()
    g["units"] = (g["packs"] / n).round(1)
    g["net_px"] = g["rev"] / g["packs"]
    g["mrp"], g["trade"] = g["w_mrp"] / g["packs"], g["w_trade"] / g["packs"]
    g["offer_rs"], g["gst"] = g["w_offer"] / g["packs"], g["w_gst"] / g["packs"]
    g["on_rs"], g["off_rs"] = g["w_on"] / g["packs"], g["w_offrs"] / g["packs"]
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
    mis, mis_have, year_known = pick_mis_months(mis_all, cost_months)
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

    if costs_other:     # the MIS is another month's, so its totals and prices cannot be compared with this billing
        rec, check = [], {"skipped": f"Costs come from {month_text(cost_month)}, not the billing month, so billing and the MIS are not compared."}
    else:
        rec = reconcile(final, mis, basis)
        check = price_check(g.loc[final.index, ["sku", "SalesOfficeID", "grp", "family", "name", "units", "net_px"]], mis)
        check["mix"] = mix_check(everyone, mis)
    offer_check = offer_price_check(g.loc[final.index])
    stages = {"total": float(total_rev), "unreadable": unreadable_rev,
              "not_in_offer": float(total_rev) - unreadable_rev - in_offer_rev, "outside_groups": outside_rev,
              "in_mis_scope": in_offer_rev - outside_rev,
              "modelled": float((final["units"] * final["net_px"]).sum() * n)}
    stages["end_drop"] = stages["in_mis_scope"] - stages["modelled"]
    gap_lines = b[[c for c in ("chain", "sku", "name", "family", "BillMonth", "packs", "rev", "mrp", "trade", "gst",
                               "offer_rs", "on_rs", "off_rs") if c in b.columns]]
    try:        # every SKU's gap against the offer file, to open in Excel and take to whoever owns the prices
        integrity.sku_gap_table({"lines": gap_lines, "n": n}).to_csv(out_dir / "sku_gap.csv", index=False)
    except Exception:
        pass
    try:
        integrity_report = integrity.run({
            "raw": raw, "off": off, "g": g.loc[final.index], "mis": mis, "everyone": everyone, "n": n, "stages": stages,
            "base_months": base_months, "monthly": monthly, "today": date.today(), "groups": MIS_GROUPS,
            "names": sku_names, "pack_size": pack_size, "dropped_pairs": dropped_pairs,
            "cost_source": cost_source, "gap": gap_breakdown, "offer_months": offer_months,
            "lines": gap_lines, "costs_other": costs_other, "cost_month": cost_month, "mtd_days": mtd_days})
    except Exception:       # a diagnostic must never stop a build
        integrity_report = None
    meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "base_months": base_months,
        "rows": int(len(final)),
        "net_revenue_cr": round(float((final["units"] * final["net_px"]).sum() / 1e7), 2),
        "quality": {"billing_revenue_with_unreadable_pack_size_pct": round(unreadable_pct * 100, 2),
                    "pack_size_taken_from_offer_name_pct": round(from_name_pct * 100, 2),
                    "billing_revenue_in_offer_scope_pct": round(in_scope_pct * 100, 1),
                    "mf_ecom_revenue_in_offer_scope_pct": round(in_scope_mf_pct * 100, 1),
                    "billing_revenue_outside_mis_groups_pct": round(outside_pct * 100, 2),
                    "revenue_with_price_gap_clipped_pct": round(clipped_pct * 100, 2),
                    "offer_lines_dropped_missing_terms": terms_dropped,
                    "rows_dropped_invalid_numbers": rows_invalid,
                    "cogs_source_share": cost_source},
        "period": period,
        "costs_from": month_text(cost_month) if costs_other else None,
        "mis_months": {"used": cost_months, "available": mis_have, "year_known": year_known,
                       "month_column": mis_cols.get("month_column"), "year_column": mis_cols.get("year_column")},
        "reconciliation": rec,
        "price_check": check,
        "offer_price_check": offer_check,
        "gap_breakdown": gap_breakdown,
        "offer_months": offer_months,
        "integrity": integrity_report,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    say("Done", 1.0)
    return meta
