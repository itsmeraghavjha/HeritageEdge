"""Loads the dataset HeritageEdge runs on.

Real data     data/rows.csv         built inside the app from SQL billing + the offer file + the MIS.
                                    Confidential. Ignored by git. Never commit it.
Sample data   sample_data/rows.csv  synthetic, in git. Used whenever no real data has been built yet.

Grain: one row per chain x customer group x sales office x SKU, with the baseline monthly packs and
the commercial terms and attributed costs behind them. Team inputs that are safe to share live in
config/ (elasticity per product family, and how each MIS cost is attributed).
"""
import contextvars
import csv
import json
import os
import re
import shutil
import sys
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

# Every built period (Oct, Nov so far, ...) is kept as its own dataset in data/periods/<key>/. A request works on one of
# them: the one the person chose (see use()), else the newest built. The names ROWS, BASIS, OPTIONS, COMBOS, LABEL, MODE
# and META are read as data.ROWS and so on; they always answer for the dataset in use, so nothing else needs to know.
SAMPLE_KEY = "sample"
_DATASETS = {}                      # key -> Dataset
_DEFAULT = SAMPLE_KEY               # key shown to anyone who has not chosen
_CHOSEN = contextvars.ContextVar("heritageedge_period", default=None)
_FIELDS = ("ROWS", "BASIS", "OPTIONS", "COMBOS", "LABEL", "MODE", "META")


class Dataset:
    """One built period: its rows and everything derived from them."""

    def __init__(self, key, rows, basis, options, label, combos, mode, meta):
        self.key, self.ROWS, self.BASIS, self.OPTIONS = key, rows, basis, options
        self.LABEL, self.COMBOS, self.MODE, self.META = label, combos, mode, meta
        self.base_metrics = None    # filled by simulate on first use


def current():
    """The dataset this request works on."""
    return _DATASETS.get(_CHOSEN.get()) or _DATASETS[_DEFAULT]


def __getattr__(name):              # data.ROWS, data.META ... answer for the dataset in use
    if name in _FIELDS:
        return getattr(current(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def use(key):
    """Chooses the period for the current request (and thread). An unknown or empty key means the default."""
    return _CHOSEN.set(key if key in _DATASETS else None)


def release(token):
    _CHOSEN.reset(token)


def period_key(base_months):
    """'2026-10' for October; '2026-08_2026-09' for an average of two months."""
    return "_".join(m[:7] for m in sorted(base_months))


def periods_dir():
    return DATA_DIR / "periods"


def period_dir(key):
    return periods_dir() / key


def period_label(meta, short=False):
    """'Oct 2026', 'Nov 2026 so far (to 13 Nov)', 'Jul to Sep 2026 (average)'."""
    months = months_text(meta.get("base_months", []))
    period = meta.get("period") or {}
    if period.get("mtd") and period.get("days"):
        return f"{months} so far ({period['days']} days)" if short else f"{months} so far, {period['days']} days of billing"
    return f"{months} (average)" if len(meta.get("base_months", [])) > 1 else months


def periods():
    """The built periods, newest month first, for the switcher."""
    out = []
    for key, ds in _DATASETS.items():
        if ds.MODE != "actual":
            continue
        meta = ds.META
        out.append({"key": key, "label": period_label(meta, short=True), "built_at": meta.get("built_at"),
                    "mtd": bool((meta.get("period") or {}).get("mtd")), "costs_from": meta.get("costs_from"),
                    "months": meta.get("base_months", []), "default": key == _DEFAULT})
    return sorted(out, key=lambda p: (max(p["months"] or [""]), p["built_at"] or ""), reverse=True)


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def pack_qty(text):
    """Quantity in one pack, in its own unit: ('400 GMS') -> (0.4, 'kg'), ('500 ML') -> (0.5, 'L'),
    ('1 KG') -> (1.0, 'kg'), ('1.5 LTR') -> (1.5, 'L'), ('700+700 ML') -> (1.4, 'L').
    Litres stay litres and kilograms stay kilograms; nothing is converted between them.
    Returns (0.0, '') when the text cannot be read."""
    t = str(text or "").upper()
    for a in ("LITRES", "LITERS", "LITRE", "LITER", "LTR"):
        t = t.replace(a, "L")
    nums = re.findall(r"\d+\.?\d*", t)
    unit = re.sub(r"[\d.+\s]", "", t)
    if not nums:
        return 0.0, ""
    tot = sum(float(n) for n in nums)
    if unit.startswith("ML"):
        return tot / 1000, "L"
    if unit.startswith("L"):
        return tot, "L"
    if unit.startswith("GM") or unit == "G":
        return tot / 1000, "kg"
    if unit.startswith("KG"):
        return tot, "kg"
    return 0.0, ""


_NAME_QTY = re.compile(r"(\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)*)\s*(ML|LTRS?|LITRES?|LITERS?|L|GMS?|G|KGS?)\b", re.I)


def pack_qty_in_name(name):
    """Same as pack_qty, for a SKU name such as 'GHEE-COW-500 ML-PP' or 'Toned milk 500 ml'."""
    m = _NAME_QTY.search(str(name or ""))
    return pack_qty(m.group(1) + m.group(2)) if m else (0.0, "")


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


def _load_meta(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
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


def days_per_month():
    """Average number of days in the base months: divides a monthly run-rate into per-day figures (LPD, KGPD).
    30 when the months are unknown (the sample data). For a month to date it is the days of billing the month holds."""
    import calendar
    me = sys.modules[__name__]
    period = me.META.get("period") or {}
    if period.get("mtd") and period.get("days"):
        return float(period["days"])
    days = []
    for m in me.META.get("base_months", []) or []:
        try:
            d = datetime.strptime(m[:10], "%Y-%m-%d")
        except ValueError:
            continue
        days.append(calendar.monthrange(d.year, d.month)[1])
    return sum(days) / len(days) if days else 30.0


def status():
    """Short description of the data in use, for the sidebar and the data page."""
    me = sys.modules[__name__]
    if me.MODE == "actual":
        months = months_text(me.META.get("base_months", []))
        return {"mode": me.MODE, "title": "Actual data",
                "detail": (f"{period_label(me.META)} billing, offer terms and MIS costs."
                           if months else "Billing, offer terms and MIS costs.")}
    return {"mode": me.MODE, "title": "Sample data",
            "detail": "Figures are illustrative monthly run-rates, not actuals."}


def _move_old_build():
    """Before periods were kept, one dataset sat in data/rows.csv. Move it into its own period folder."""
    old = DATA_DIR / "rows.csv"
    if not old.exists():
        return
    meta = _load_meta(DATA_DIR / "meta.json")
    key = period_key(meta["base_months"]) if meta.get("base_months") else "earlier"
    target = period_dir(key)
    if (target / "rows.csv").exists():
        return                                   # a newer build already holds this period: leave the old files alone
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("meta.json", "sku_gap.csv", "rows.csv"):          # rows.csv last: it marks the move as done
            if (DATA_DIR / name).exists():
                try:
                    shutil.copy2(DATA_DIR / name, target / name)
                except OSError:
                    if name == "rows.csv":
                        raise
        for name in ("rows.csv", "meta.json", "sku_gap.csv"):
            try:
                (DATA_DIR / name).unlink()
            except OSError:
                pass                             # for example open in Excel on Windows: the old copy is simply left
    except OSError:
        return                                   # never stop the app from starting; the old build is read next start


def _make(key, rows_path, meta, mode, elasticity, basis):
    rows = []
    for r in _read(rows_path):
        for k in NUM:
            r[k] = float(r[k])
        r["e"] = elasticity.get(r["family"], DEFAULT_ELASTICITY)
        r["so_name"] = r.get("so_name") or r["so"]
        r["name"] = r.get("name") or r["sku"]
        r["city"] = r.get("city") or _city(r["so_name"])
        for k in ("grp", "variant", "pack"):
            r[k] = r.get(k) or ""
        r["size"], r["qty_unit"] = pack_qty(r["pack"])
        if not r["qty_unit"]:      # a dataset built before pack sizes were kept: read it from the SKU name
            r["size"], r["qty_unit"] = pack_qty_in_name(r["name"])
        rows.append(r)

    options, label = {}, {}
    for dim in DIM_IDS:
        seen = {}
        for r in rows:
            seen.setdefault(r[dim], _label(dim, r))
        label[dim] = seen
        options[dim] = [{"id": k, "name": v}
                        for k, v in sorted(seen.items(), key=lambda kv: str(kv[1]).lower())]
    combos = sorted({tuple(str(r[d]) for d in DIM_IDS) for r in rows})
    return Dataset(key, rows, basis, options, label, combos, mode, meta)


def reload():
    """(Re)read every built period. Called at import and again after a dataset is built.
    With nothing built, the sample data is the only dataset."""
    global _DATASETS, _DEFAULT
    elasticity = {r["family"]: float(r["elasticity"]) for r in _read(CONFIG_DIR / "assumptions.csv")}
    basis = {r["line"]: r["basis"] for r in _read(CONFIG_DIR / "attribution.csv")}

    found = {}
    if os.environ.get("HERITAGEEDGE_USE_SAMPLE") != "1":
        _move_old_build()
        for folder in sorted(periods_dir().glob("*")) if periods_dir().exists() else []:
            if (folder / "rows.csv").exists():
                found[folder.name] = _make(folder.name, folder / "rows.csv", _load_meta(folder / "meta.json"),
                                           "actual", elasticity, basis)
    if not found:
        found[SAMPLE_KEY] = _make(SAMPLE_KEY, SAMPLE_DIR / "rows.csv", {}, "sample", elasticity, basis)
    _DATASETS = found
    # Anyone who has not chosen sees the latest month (the newest build of it if there are several kinds)
    _DEFAULT = max(found, key=lambda k: (max(found[k].META.get("base_months") or [""]), found[k].META.get("built_at") or ""))


reload()
