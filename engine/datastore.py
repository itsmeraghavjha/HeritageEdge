"""Everything the Data page needs: uploaded files, billing pulls, dataset builds and their progress.

All files live in data/ (ignored by git). Slow steps run in one background thread so the page stays responsive.
"""
import json
import threading
import traceback
from datetime import datetime
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
def uploaded(kind):
    info = _read_json(uploads_dir() / "manifest.json").get(kind)
    if info and (uploads_dir() / info["stored"]).exists():
        return info
    return None


def upload_path(kind):
    info = uploaded(kind)
    return uploads_dir() / info["stored"] if info else None


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
    for old in folder.glob(f"{kind}.*"):
        old.unlink()
    stored = f"{kind}{ext}"
    target = folder / stored
    storage.save(target)
    manifest = _read_json(folder / "manifest.json")
    manifest[kind] = {"name": Path(name).name, "stored": stored, "size": target.stat().st_size,
                      "uploaded_at": datetime.now().isoformat(timespec="seconds")}
    _write_json(folder / "manifest.json", manifest)
    return manifest[kind]


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
def pull_billing(start):
    def job(progress):
        summary = sql_source.fetch_billing(start, progress)
        _write_json(data.DATA_DIR / "billing_meta.json", summary)
    return start_job("pull", job)


def build(base_months):
    def job(progress):
        offer, mis = upload_path("offer"), upload_path("mis")
        if not offer:
            raise prepare.DataError("Upload the offer file first.")
        if not mis:
            raise prepare.DataError("Upload the MIS workbook first.")
        prepare.build_dataset(offer, mis, sql_source.billing_path(), base_months, progress)
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
        "billing": billing,
        "suggested_months": prepare.default_base_months(months),
        "sql": {"missing": sql_source.missing_settings(),
                "default_start": sql_source.default_start().isoformat()},
        "job": job_status(),
    }
