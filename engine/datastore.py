"""Everything the Data page needs: uploaded files, billing pulls, dataset builds and their progress.

All files live in data/ (ignored by git). Slow steps run in one background thread so the page stays responsive.
"""
import json
import threading
import traceback
from datetime import date, datetime
from pathlib import Path

from . import data, prepare, simulate, sql_source

ALLOWED = {"offer": (".xlsb", ".xlsx", ".xlsm"), "mis": (".xlsx", ".xlsm")}
KIND_NAMES = {"offer": "offer file", "mis": "MIS workbook"}


SPACES = ("month", "projection")


def root(space="month"):
    """Each tab of the Data page has its own folder of uploads and billing: the month tab uses data/, the projection tab
    data/projection/. Builds of both go to data/periods/ and carry the flag that says which tab made them."""
    return data.DATA_DIR / "projection" if space == "projection" else data.DATA_DIR


def uploads_dir(space="month"):
    return root(space) / "uploads"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2), encoding="utf-8")


# ---------- uploads ----------
# One MIS workbook is kept. Offer files are kept for several months (one per period they cover), because each month has
# its own file and an earlier month may need building again.
MAX_OFFER_FILES = 1                     # one offer file per tab: a new upload replaces it


def _manifest(space="month"):
    return _read_json(uploads_dir(space) / "manifest.json")


def offers(space="month"):
    """The stored offer files, oldest first: name, stored, size, uploaded_at and the months each has a column for."""
    m = _manifest(space)
    items = list(m.get("offers") or ([m["offer"]] if m.get("offer") else []))
    items = [i for i in items if (uploads_dir(space) / i["stored"]).exists()][-MAX_OFFER_FILES:]
    fill = [i for i in items if "months" not in i]                       # uploaded before the months were read
    if fill:
        for i in fill:
            i["months"] = prepare.offer_file_months(uploads_dir(space) / i["stored"])
        m["offers"] = items
        m.pop("offer", None)
        _write_json(uploads_dir(space) / "manifest.json", m)
    return items


def uploaded(kind, space="month"):
    """The MIS file, or for the offer file the newest one."""
    if kind == "offer":
        items = offers(space)
        return items[-1] if items else None
    info = _manifest(space).get(kind)
    if info and (uploads_dir(space) / info["stored"]).exists():
        return info
    return None


def upload_path(kind, space="month"):
    info = uploaded(kind, space)
    return uploads_dir(space) / info["stored"] if info else None


def offer_choice(base_months, space="month"):
    """Which offer file a build of these months uses, and how good a fit it is.

    A file's price columns (MRP, trade margin, GST) hold its latest month, so the best file for a month is the one whose
    latest month is that month. Next best is the nearest later file, which still has the month's own offer column.
    Returns (stored item or None, fit) with fit 'exact', 'later' (prices are from a later month) or 'none' (no file has
    a column for the month, so the newest file is used and the data checks say so)."""
    items = offers(space)
    if not items:
        return None, "none"
    want = sorted(base_months)
    if len(want) == 1:
        m = want[0]
        exact = [i for i in items if i.get("months") and max(i["months"]) == m]
        if exact:
            return exact[-1], "exact"
        later = [i for i in items if m in (i.get("months") or [])]
        if later:
            nearest = min(max(i["months"]) for i in later)
            return [i for i in later if max(i["months"]) == nearest][-1], "later"
        return items[-1], "none"
    fits = [i for i in items if set(want) <= set(i.get("months") or [])]
    return (fits or items)[-1], ("later" if fits else "none")


def pick_offer(base_months, space="month"):
    item, _ = offer_choice(base_months, space)
    return uploads_dir(space) / item["stored"] if item else None


def save_upload(kind, storage, space="month"):
    """Saves an uploaded file (a werkzeug FileStorage). Raises prepare.DataError for a wrong file type."""
    if kind not in ALLOWED:
        raise prepare.DataError("Unknown file type.")
    name = storage.filename or ""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED[kind]:
        raise prepare.DataError(f"The {KIND_NAMES[kind]} must be one of: {', '.join(ALLOWED[kind])}.")
    folder = uploads_dir(space)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(space)
    if kind == "mis":
        for old in folder.glob("mis.*"):
            old.unlink()
        stored = f"mis{ext}"
    else:
        manifest["offers"] = offers(space)
        stored = f"offer_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{ext}"
    target = folder / stored
    storage.save(target)
    info = {"name": Path(name).name, "stored": stored, "size": target.stat().st_size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds")}
    if kind == "mis":
        manifest["mis"] = info
    else:
        info["months"] = prepare.offer_file_months(target)
        manifest["offers"] = [info]                                          # the new file replaces the old one
        for gone in folder.glob("offer*"):                                    # files dropped from the list, and the old single file
            if gone.name not in {i["stored"] for i in manifest["offers"]}:
                gone.unlink(missing_ok=True)
        manifest.pop("offer", None)
    _write_json(folder / "manifest.json", manifest)
    return info


# ---------- background jobs ----------
_lock = threading.Lock()
_job = {"state": "idle", "name": None, "message": "", "fraction": 0.0, "error": None,
        "started_at": None, "finished_at": None}


def job_status():
    with _lock:
        return dict(_job)


def _progress(message, fraction=None):
    with _lock:
        _job["message"] = message
        if fraction is not None:
            _job["fraction"] = max(_job["fraction"], min(1.0, fraction))


def start_job(name, fn):
    """Runs fn(progress) in a thread. Returns False if another job is still running."""
    with _lock:
        if _job["state"] == "running":
            return False
        _job.update(state="running", name=name, message="Starting", fraction=0.0, error=None,
                    started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None)

    def run():
        try:
            fn(_progress)
            state, error = "done", None
        except (prepare.DataError, sql_source.SqlError) as exc:
            state, error = "failed", str(exc)
        except Exception as exc:  # unexpected: keep the details in the server log
            traceback.print_exc()
            state, error = "failed", f"Something went wrong: {sql_source._clean(exc)}"
        with _lock:
            _job.update(state=state, error=error, finished_at=datetime.now().isoformat(timespec="seconds"))

    threading.Thread(target=run, daemon=True).start()
    return True


# ---------- the two jobs ----------
def pull_billing(start, end=None, space="month"):
    def job(progress):
        summary = sql_source.fetch_billing(start, progress, end=end, space=space)
        _write_json(root(space) / "billing_meta.json", summary)
    return start_job("pull", job)


def build(base_months, cost_month=None, mtd_days=None, space="month"):
    def job(progress):
        mis = upload_path("mis", space)
        item, fit = offer_choice(base_months, space)
        if not item:
            raise prepare.DataError("Upload the offer file first.")
        offer = uploads_dir(space) / item["stored"]
        if not mis:
            raise prepare.DataError("Upload the MIS workbook first.")
        prepare.build_dataset(offer, mis, sql_source.billing_path(space), base_months, progress,
                              out_dir=data.period_dir(data.period_key(base_months)),
                              cost_month=cost_month, mtd_days=mtd_days, offer_name=item["name"], offer_fit=fit)
        data.reload()
        simulate.refresh()
    return start_job("build", job)


# ---------- status for the page ----------
def _billing_info(space="month"):
    path = sql_source.billing_path(space)
    if not path.exists():
        return None
    info = _read_json(root(space) / "billing_meta.json")
    if not info.get("months"):
        info["months"] = prepare.billing_months(path)
        info.setdefault("pulled_at", datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"))
    return info


def _out_of_date(period, billing, space="month"):
    """What changed after this period was built, in plain words (empty when nothing did)."""
    built = period.get("built_at") or ""
    reasons = []
    if billing and (billing.get("pulled_at") or "") > built:
        reasons.append("billing was pulled again")
    mis = uploaded("mis", space)
    if mis and mis["uploaded_at"] > built:
        reasons.append("the MIS was replaced")
    want = set(period.get("months") or [])
    if any(o["uploaded_at"] > built and want <= set(o.get("months") or []) for o in offers(space)):
        reasons.append("the offer file was replaced")
    return reasons


def _offer_for(month, space="month"):
    """The offer file a build of this month would use: its name, the latest month its prices are from, and the fit."""
    item, fit = offer_choice([month], space)
    if not item:
        return {"name": None, "fit": "none", "latest": None}
    return {"name": item["name"], "fit": fit, "latest": max(item["months"]) if item.get("months") else None}


def status(space="month"):
    billing = _billing_info(space)
    months = billing["months"] if billing else []
    built = dict(data.META) if data.MODE == "actual" else {}
    mine = [p for p in data.periods() if bool(p.get("mtd")) == (space == "projection") and not p.get("forecast")]
    return {
        "space": space,
        "data": {**data.status(), **built},
        "uploads": {k: uploaded(k, space) for k in ALLOWED},
        "offers": offers(space),
        "offer_for": {m: _offer_for(m, space) for m in months},
        "periods": [{**p, "changed": _out_of_date(p, billing, space)} for p in mine],
        "period_key": data.current().key.removesuffix(data.FORECAST),     # the forecast belongs to its build
        "billing": billing,
        "suggested_months": prepare.default_base_months(months),
        "sql": {"missing": sql_source.missing_settings(),
                "default_start": sql_source.default_start().isoformat(),
                "default_end": date.today().isoformat()},
        "job": job_status(),
    }
