import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for)

from engine import env  # noqa: F401  (loads .env before anything reads settings)
from engine import data, datastore, export, mailer, network, prepare, simulate, sql_source, users

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3          # the MIS workbook can be large
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _secret_key():
    """Signs the login cookie. A key in .env wins. Otherwise one is made once and kept in data/ (ignored by git), so
    people stay logged in when the app restarts. The placeholder "change-me" from .env.example is never used."""
    key = os.environ.get("SECRET_KEY", "")
    if key and key != "change-me":
        return key
    path = data.DATA_DIR / "secret.key"
    try:
        if path.exists():
            return path.read_text().strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        return path.read_text().strip()
    except OSError:
        return os.urandom(24)


app.secret_key = _secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)


def auth_enabled():
    """Login is on as soon as the first user exists. With no users the app stays open on this computer only."""
    return users.has_users()


# ---------- login with email and password ----------
OPEN_PAGES = ("login", "forgot", "set_password", "static")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


@app.before_request
def require_login():
    g.user = None
    if request.method == "POST" and not request.path.startswith("/api/"):
        # forms carry a token; the /api/ calls are JSON with a SameSite=Lax cookie, which other sites cannot send
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "-")):
            session.pop("uid", None)
            return render_template("message.html", title="Please try again",
                                   text="The page was open for too long. Go back and reload it."), 400
    if not auth_enabled():
        return None
    uid = session.get("uid")
    user = users.get(uid) if uid else None
    if user and user["active"]:       # looked up on every request, so disabling someone takes effect at once
        g.user = user
        return None
    session.pop("uid", None)
    if request.endpoint in OPEN_PAGES:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Please log in."), 401
    return redirect(url_for("login"))


def admin_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user or g.user["role"] != "admin":
            return render_template("message.html", title="Admins only",
                                   text="Only the admin can manage users."), 403
        return view(*args, **kwargs)
    return wrapper


def set_password_link(token):
    base = (os.environ.get("APP_BASE_URL") or request.host_url).rstrip("/")
    return base + url_for("set_password", token=token)


def send_link(user, purpose):
    """Emails a set-password link. Returns (sent, link): the link is for the admin to copy when email fails."""
    token = users.issue_token(user["id"], purpose)
    link = set_password_link(token)
    hours = users.INVITE_HOURS if purpose == "invite" else users.RESET_HOURS
    greeting = f"Hello {user['name']}," if user["name"] else "Hello,"
    if purpose == "invite":
        subject, lead = "Your HeritageEdge account", "An account has been created for you on HeritageEdge."
    else:
        subject, lead = "Reset your HeritageEdge password", "Someone asked to reset your HeritageEdge password."
    body = (f"{greeting}\n\n{lead} Set your password here:\n\n{link}\n\n"
            f"The link works once and expires in {hours} hours. If you did not expect this email, ignore it.\n")
    ok, _ = mailer.send(user["email"], subject, body)
    return ok, link


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled() or g.user:
        return redirect(url_for("simulator_page"))
    error = None
    if request.method == "POST":
        try:
            user = users.login(request.form.get("email", ""), request.form.get("password", ""))
        except users.UserError as exc:
            time.sleep(0.5)
            error = str(exc)
        else:
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            return redirect(url_for("simulator_page"))
    return render_template("login.html", error=error, notice=request.args.get("notice"),
                           email=request.form.get("email", ""))


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    sent = False
    if request.method == "POST":
        user = users.get_by_email(request.form.get("email", ""))
        if user and user["active"]:
            send_link(user, "reset")
        sent = True          # the same answer whether or not the email has an account
    return render_template("forgot.html", sent=sent)


@app.route("/set-password/<token>", methods=["GET", "POST"])
def set_password(token):
    user = users.token_user(token)
    if not user:
        return render_template("message.html", title="This link no longer works",
                               text="It has expired or was already used.", link=("/forgot", "Get a new link")), 410
    error = None
    if request.method == "POST":
        if request.form.get("password") != request.form.get("again"):
            error = "The two passwords are not the same."
        else:
            try:
                users.set_password_with_token(token, request.form.get("password", ""))
            except users.UserError as exc:
                error = str(exc)
            else:
                session.clear()
                return redirect(url_for("login", notice="Password saved. Log in with your new password."))
    return render_template("set_password.html", error=error, email=user["email"], min_length=users.MIN_PASSWORD)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- admin: people ----------
@app.get("/admin/users")
@admin_only
def admin_users():
    return render_template("users.html", active="users", people=users.list_users(),
                           email_ready=mailer.configured(), message=session.pop("people_msg", None))


def _done(text, link=None):
    session["people_msg"] = {"text": text, "link": link}
    return redirect(url_for("admin_users"))


@app.post("/admin/users/new")
@admin_only
def admin_users_new():
    try:
        uid = users.create(request.form.get("email", ""), request.form.get("name", ""))
    except users.UserError as exc:
        return _done(str(exc))
    user = users.get(uid)
    sent, link = send_link(user, "invite")
    if sent:
        return _done(f"Account created. A link to set a password was emailed to {user['email']}.")
    return _done(f"Account created, but the email could not be sent. Send {user['email']} this link yourself "
                 f"(it works once and expires in {users.INVITE_HOURS} hours):", link)


@app.post("/admin/users/<int:uid>/active")
@admin_only
def admin_users_active(uid):
    on = request.form.get("active") == "1"
    try:
        users.set_active(uid, on)
    except users.UserError as exc:
        return _done(str(exc))
    user = users.get(uid)
    return _done(f"{user['email']} is now {'enabled' if on else 'disabled'}.")


@app.post("/admin/users/<int:uid>/link")
@admin_only
def admin_users_link(uid):
    user = users.get(uid)
    if not user or not user["active"]:
        return _done("Enable the account first.")
    sent, link = send_link(user, "invite")
    if sent:
        return _done(f"A new link was emailed to {user['email']}.")
    return _done(f"The email could not be sent. Send {user['email']} this link yourself:", link)


@app.after_request
def no_store(response):
    """Numbers and file details are confidential, so browsers should not keep copies of API answers."""
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def inject_globals():
    return {"data_status": data.status(), "auth_enabled": auth_enabled(), "current_user": g.get("user"),
            "csrf_token": csrf_token,
            "network_svg": lambda: network.svg(network.labels(), url_for("static", filename="img/heritage-logo.png"))}


@app.template_filter("datetime")
def _datetime(ts):
    return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M")


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
    # the admin account exists. Debug mode exposes a code console, so it is never allowed beyond this computer.
    if host not in LOCAL_HOSTS and not auth_enabled():
        raise SystemExit(f"Refusing to listen on {host} before an admin exists. Run: python manage.py create-admin "
                         "you@company.com, or leave HOST unset to keep the app on this computer only.")
    if host not in LOCAL_HOSTS and debug:
        raise SystemExit("Refusing to run debug mode on a network address.")
    app.run(host=host, port=int(os.environ.get("PORT", 5000)), debug=debug)
