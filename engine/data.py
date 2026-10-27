"""Loads the dataset HeritageEdge runs on.

Real data     data/rows.csv         built inside the app from SQL billing + the offer file + the MIS.
                                    Confidential. Ignored by git. Never commit it.
Sample data   sample_data/rows.csv  synthetic, in git. Used whenever no real data has been built yet.

Grain: one row per chain x customer group x sales office x SKU, with the baseline monthly packs and
the commercial terms and attributed costs behind them. Team inputs that are safe to share live in
config/ (elasticity per product family, and how each MIS cost is attributed).
"""
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HERITAGEEDGE_DATA_DIR", ROOT / "data"))
SAMPLE_DIR = ROOT / "sample_data"
CONFIG_DIR = ROOT / "config"

LINES = ("cogs", "sd", "pa", "interest", "ou", "mktg")
NUM = ("units", "mrp_x", "trade", "promo_rate", "other_rate", "net_px") + LINES
DEFAULT_ELASTICITY = -1.0
NOT_RECORDED = "(not recorded)"

# id, label. The id is also the key used in API requests (scope, filters, dims) and in each row.
DIM_DEFS = (
    ("chain", "Chain"),
    ("grp", "Customer group"),
    ("state", "State"),
    ("city", "City"),
    ("so", "Sales office"),
    ("family", "Product category"),
    ("variant", "Variant"),
    ("pack", "Pack size"),
    ("sku", "SKU"),
)
DIM_IDS = tuple(d for d, _ in DIM_DEFS)

# Filled by reload(): ROWS, BASIS, OPTIONS, LABEL, MODE, META
ROWS = []
BASIS = {}
OPTIONS = {}
COMBOS = []     # every distinct combination of dimension values in the data, in DIM_IDS order (as text)
LABEL = {}
MODE = "sample"
META = {}


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _city(so_name):
    """Older files have no city column: take it from the sales office name
    ('Bangalore Sales Office - 3' -> 'Bangalore'). Approximate."""
    name = re.sub(r"\s+(sales\s*office|so)\b.*$", "", (so_name or "").strip(), flags=re.I).strip()
    return name.title() or (so_name or "").title()


def _label(dim, row):
    if dim == "so":
        return row["so_name"] or row["so"]
    if dim == "sku":
        return row["name"] or row["sku"]
    return row[dim] or NOT_RECORDED


def _load_meta(mode):
    path = DATA_DIR / "meta.json"
    if mode == "actual" and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def months_text(months):
    """['2026-07-01', '2026-08-01', '2026-09-01'] -> 'Jul to Sep 2026'."""
    if not months:
        return ""
    dts = [datetime.strptime(m[:10], "%Y-%m-%d") for m in sorted(months)]
    first, last = dts[0], dts[-1]
    if len(dts) == 1:
        return first.strftime("%b %Y")
    if first.year == last.year:
        return f"{first.strftime('%b')} to {last.strftime('%b %Y')}"
    return f"{first.strftime('%b %Y')} to {last.strftime('%b %Y')}"


def status():
    """Short description of the data in use, for the sidebar and the data page."""
    if MODE == "actual":
        months = months_text(META.get("base_months", []))
        return {"mode": MODE, "title": "Actual data",
                "detail": (f"{months} billing, offer terms and MIS costs. Monthly run-rates."
                           if months else "Billing, offer terms and MIS costs. Monthly run-rates.")}
    return {"mode": MODE, "title": "Sample data",
            "detail": "Figures are illustrative monthly run-rates, not actuals."}


def reload():
    """(Re)read the dataset. Called at import and again after a new dataset is built."""
    global ROWS, BASIS, OPTIONS, LABEL, MODE, META, COMBOS
    real = DATA_DIR / "rows.csv"
    use_real = real.exists() and os.environ.get("HERITAGEEDGE_USE_SAMPLE") != "1"
    path = real if use_real else SAMPLE_DIR / "rows.csv"

    elasticity = {r["family"]: float(r["elasticity"]) for r in _read(CONFIG_DIR / "assumptions.csv")}
    basis = {r["line"]: r["basis"] for r in _read(CONFIG_DIR / "attribution.csv")}

    rows = []
    for r in _read(path):
        for k in NUM:
            r[k] = float(r[k])
        r["e"] = elasticity.get(r["family"], DEFAULT_ELASTICITY)
        r["so_name"] = r.get("so_name") or r["so"]
        r["name"] = r.get("name") or r["sku"]
        r["city"] = r.get("city") or _city(r["so_name"])
        for k in ("grp", "variant", "pack"):
            r[k] = r.get(k) or ""
        rows.append(r)

    options, label = {}, {}
    for dim in DIM_IDS:
        seen = {}
        for r in rows:
            seen.setdefault(r[dim], _label(dim, r))
        label[dim] = seen
        options[dim] = [{"id": k, "name": v}
                        for k, v in sorted(seen.items(), key=lambda kv: str(kv[1]).lower())]

    mode = "actual" if use_real else "sample"
    COMBOS = sorted({tuple(str(r[d]) for d in DIM_IDS) for r in rows})
    ROWS, BASIS, OPTIONS, LABEL, MODE = rows, basis, options, label, mode
    META = _load_meta(mode)


reload()
