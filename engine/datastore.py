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


def uploads_dir():
    return data.DATA_DIR / "uploads"


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
MAX_OFFER_FILES = 8


def _manifest():
    return _read_json(uploads_dir() / "manifest.json")


def offers():
    """The stored offer files, oldest first: name, stored, size, uploaded_at and the months each has a column for."""
    m = _manifest()
    items = list(m.get("offers") or ([m["offer"]] if m.get("offer") else []))
    return [i for i in items if (uploads_dir() / i["stored"]).exists()]


def uploaded(kind):
    """The MIS file, or for the offer file the newest one."""
    if kind == "offer":
        items = offers()
        return items[-1] if items else None
    info = _manifest().get(kind)
    if info and (uploads_dir() / info["stored"]).exists():
        return info
    return None


def upload_path(kind):
    info = uploaded(kind)
    return uploads_dir() / info["stored"] if info else None


def pick_offer(base_months):
    """The offer file for a build: the newest one that has a column for every month being built, else the newest."""
    items = offers()
    if not items:
        return None
    want = set(base_months)
    fits = [i for i in items if want <= set(i.get("months") or [])]
    return uploads_dir() / (fits or items)[-1]["stored"]


def save_upload(kind, storage):
    """Saves an uploaded file (a werkzeug FileStorage). Raises prepare.DataError for a wrong file type."""
    if kind not in ALLOWED:
        raise prepare.DataError("Unknown file type.")
    name = storage.filename or ""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED[kind]:
        raise prepare.DataError(f"The {KIND_NAMES[kind]} must be one of: {', '.join(ALLOWED[kind])}.")
    folder = uploads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    manifest = _manifest()
    if kind == "mis":
        for old in folder.glob("mis.*"):
            old.unlink()
        stored = f"mis{ext}"
    else:
        manifest["offers"] = offers()
        stored = f"offer_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{ext}"
    target = folder / stored
    storage.save(target)
    info = {"name": Path(name).name, "stored": stored, "size": target.stat().st_size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds")}
    if kind == "mis":
        manifest["mis"] = info
    else:
        info["months"] = prepare.offer_file_months(target)
        kept = []
        for old in manifest["offers"]:
            same = info["months"] and old.get("months") == info["months"]     # a newer version of the same file
            if same:
                (folder / old["stored"]).unlink(missing_ok=True)
            else:
                kept.append(old)
        manifest["offers"] = (kept + [info])[-MAX_OFFER_FILES:]
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
def pull_billing(start, end=None):
    def job(progress):
        summary = sql_source.fetch_billing(start, progress, end=end)
        _write_json(data.DATA_DIR / "billing_meta.json", summary)
    return start_job("pull", job)


def build(base_months, cost_month=None, mtd_days=None):
    def job(progress):
        mis = upload_path("mis")
        offer = pick_offer(base_months)
        if not offer:
            raise prepare.DataError("Upload the offer file first.")
        if not mis:
            raise prepare.DataError("Upload the MIS workbook first.")
        prepare.build_dataset(offer, mis, sql_source.billing_path(), base_months, progress,
                              out_dir=data.period_dir(data.period_key(base_months)),
                              cost_month=cost_month, mtd_days=mtd_days)
        data.reload()
        simulate.refresh()
    return start_job("build", job)


# ---------- status for the page ----------
def _billing_info():
    path = sql_source.billing_path()
    if not path.exists():
        return None
    info = _read_json(data.DATA_DIR / "billing_meta.json")
    if not info.get("months"):
        info["months"] = prepare.billing_months(path)
        info.setdefault("pulled_at", datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"))
    return info


def status():
    billing = _billing_info()
    months = billing["months"] if billing else []
    built = dict(data.META) if data.MODE == "actual" else {}
    return {
        "data": {**data.status(), **built},
        "uploads": {k: uploaded(k) for k in ALLOWED},
        "offers": offers(),
        "periods": data.periods(),
        "period_key": data.current().key,
        "billing": billing,
        "suggested_months": prepare.default_base_months(months),
        "sql": {"missing": sql_source.missing_settings(),
                "default_start": sql_source.default_start().isoformat(),
                "default_end": date.today().isoformat()},
        "job": job_status(),
    }
