import hmac
import os
import time

from flask import (Flask, jsonify, redirect, render_template, request, send_file, session, url_for)

from engine import env  # noqa: F401  (loads .env before anything reads settings)
from engine import data, datastore, export, prepare, simulate, sql_source

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3          # the MIS workbook can be large
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _secret_key():
    """Signs the login cookie. .env.example ships the placeholder "change-me", and a published value would let
    anyone forge a login, so a missing or placeholder key is replaced by a random one (logins end on restart)."""
    key = os.environ.get("SECRET_KEY", "")
    return key if key and key != "change-me" else os.urandom(24)


app.secret_key = _secret_key()


def auth_enabled():
    return bool(os.environ.get("APP_PASSWORD"))


# ---------- optional login (set APP_PASSWORD in .env) ----------
@app.before_request
def require_login():
    if not auth_enabled() or request.endpoint in ("login", "static") or session.get("ok"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Please log in."), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("simulator_page"))
    error = None
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), os.environ["APP_PASSWORD"]):
            session["ok"] = True
            return redirect(url_for("simulator_page"))
        time.sleep(0.6)
        error = "That password is not right."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.after_request
def no_store(response):
    """Numbers and file details are confidential, so browsers should not keep copies of API answers."""
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def inject_globals():
    return {"data_status": data.status(), "auth_enabled": auth_enabled()}


# ---------- pages ----------
@app.get("/")
def simulator_page():
    return render_template("simulator.html", active="simulator")


@app.get("/pnl")
def pnl_page():
    return render_template("pnl.html", active="pnl")


@app.get("/data")
def data_page():
    return render_template("data.html", active="data")


# ---------- numbers ----------
@app.get("/api/meta")
def meta():
    return jsonify({
        "status": data.status(),
        "dims": [{"id": d, "name": name, "options": data.OPTIONS[d]} for d, name in data.DIM_DEFS],
        "combos": data.COMBOS,
    })


def _payload():
    body = request.get_json(silent=True) or {}
    return body.get("levers"), body.get("scope"), body.get("dims"), body.get("filters")


@app.post("/api/simulate")
def api_simulate():
    levers, scope, _, _ = _payload()
    return jsonify(simulate.simulate(levers, scope))


@app.post("/api/pnl")
def api_pnl():
    levers, scope, dims, flt = _payload()
    return jsonify(simulate.pnl(levers, scope, dims, flt))


@app.post("/api/export")
def api_export():
    levers, scope, dims, flt = _payload()
    buf = export.build_workbook(levers, scope, dims, flt)
    return send_file(buf, as_attachment=True, download_name="heritageedge-pnl.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- data page ----------
@app.get("/api/data/status")
def data_status():
    return jsonify(datastore.status())


@app.post("/api/data/upload/<kind>")
def data_upload(kind):
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify(error="Choose a file to upload."), 400
    try:
        info = datastore.save_upload(kind, file)
    except prepare.DataError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(info)


@app.post("/api/data/sql/test")
def data_sql_test():
    ok, message = sql_source.test_connection()
    return jsonify(ok=ok, message=message)


@app.post("/api/data/sql/pull")
def data_sql_pull():
    start = (request.get_json(silent=True) or {}).get("start") or sql_source.default_start().isoformat()
    if not datastore.pull_billing(start):
        return jsonify(error="Another step is still running."), 409
    return jsonify(started=True)


@app.post("/api/data/build")
def data_build():
    months = (request.get_json(silent=True) or {}).get("months") or []
    if not datastore.build(months):
        return jsonify(error="Another step is still running."), 409
    return jsonify(started=True)


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG") == "1"
    # The app holds confidential numbers and can pull from SQL, so it stays private to this computer unless
    # a password is set. Debug mode exposes a code console, so it is never allowed beyond this computer.
    if host not in LOCAL_HOSTS and not auth_enabled():
        raise SystemExit(f"Refusing to listen on {host} without a password. Set APP_PASSWORD in .env, "
                         "or leave HOST unset to keep the app on this computer only.")
    if host not in LOCAL_HOSTS and debug:
        raise SystemExit("Refusing to run debug mode on a network address.")
    app.run(host=host, port=int(os.environ.get("PORT", 5000)), debug=debug)
