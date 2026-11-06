import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for)

from engine import env  # noqa: F401  (loads .env before anything reads settings)
from engine import access, activity, data, datastore, export, mailer, network, prepare, simulate, sql_source, users

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
        if user["must_change"] and request.endpoint not in ("change_password", "logout", "static"):
            if request.path.startswith("/api/"):
                return jsonify(error="Choose a new password first."), 403
            return redirect(url_for("change_password"))
        return None
    session.pop("uid", None)
    if request.endpoint in OPEN_PAGES:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Please log in."), 401
    return redirect(url_for("login"))


_seen = {}
SCREEN_OF = {"simulator_page": "simulator", "pnl_page": "pnl", "data_page": "data", "admin_users": "users",
             "admin_activity": "activity"}


def log(action, detail="", user=None, email=None):
    """Records an event in the activity log. Only when people have logins, so a one-person local run stays quiet."""
    if auth_enabled():
        activity.record(user or g.get("user"), action, detail, email)


def _target(uid):
    person = users.get(uid)
    return person["email"] if person else f"#{uid}"


@app.after_request
def log_screen_views(response):
    """Counts a screen only when it was really shown: a refused page (a KAM opening Data) is not a view."""
    screen = SCREEN_OF.get(request.endpoint)
    if screen and request.method == "GET" and response.status_code == 200 and g.get("user"):
        key, now = (g.user["id"], screen), time.time()
        if now - _seen.get(key, 0) > 300:          # a reload is not a new visit
            _seen[key] = now
            log("view", screen)
    return response


def admin_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user or g.user["role"] != "admin":
            return render_template("message.html", title="Admins only",
                                   text="Only the admin can manage users."), 403
        return view(*args, **kwargs)
    return wrapper


def data_admin_only(view):
    """The Data page builds the dataset from confidential sources: the admin only (anyone, while no logins exist)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if auth_enabled() and (not g.user or g.user["role"] != "admin"):
            if request.path.startswith("/api/"):
                return jsonify(error="Only the admin can do this."), 403
            return render_template("message.html", title="Admins only", text="Only the admin can open the Data page."), 403
        return view(*args, **kwargs)
    return wrapper


def visible():
    """(allowed row positions or None, limited): what the signed-in person may see. Used by every numbers route."""
    user = g.get("user")
    return access.allowed_indices(user), access.limited(user)


def _no_accounts(active):
    """A KAM with no mapped accounts gets an explanation instead of a screen of zeros."""
    allowed, limited = visible()
    if limited and allowed is not None and len(allowed) == 0:
        return render_template("no_accounts.html", active=active)
    return None


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
            log("login_failed", error, email=request.form.get("email", "").strip().lower())
        else:
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            log("login", user=user)
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
            log("link_sent", "from Forgot password", user=user)
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
                log("password_set", user=user)
                return redirect(url_for("login", notice="Password saved. Log in with your new password."))
    return render_template("set_password.html", error=error, email=user["email"], min_length=users.MIN_PASSWORD)


@app.post("/logout")
def logout():
    log("logout")
    session.clear()
    return redirect(url_for("login"))


# ---------- admin: people ----------
@app.get("/admin/users")
@admin_only
def admin_users():
    people = users.list_users()
    for p in people:
        p["scope_count"] = len(users.scope_rows(p["id"])) if p["role"] == "kam" else 0
    counts = {"all": len(people), "kam": sum(1 for p in people if p["role"] == "kam"),
              "leadership": sum(1 for p in people if p["role"] == "leadership"),
              "pending": sum(1 for p in people if p["active"] and (p["must_change"] or not p["pw_hash"]) and not p["last_login"])}
    return render_template("users.html", active="users", people=people, counts=counts, email_ready=mailer.configured(),
                           message=session.pop("people_msg", None), unmatched=access.unmatched_clusters(),
                           chains=access.chain_names(), aliases=_alias_view())


# ---------- admin: activity ----------
PAGE = 40


@app.get("/admin/activity")
@admin_only
def admin_activity():
    group = request.args.get("type", "")
    if group not in dict(activity.GROUPS):
        group = ""
    uid = request.args.get("person", type=int)
    page = max(request.args.get("page", 1, type=int), 1)
    events, total = activity.recent(PAGE, (page - 1) * PAGE, group, uid)
    stats = activity.summary()
    people = users.list_users()
    rows = []
    for p in people:
        s = stats["people"].get(p["id"], {})
        rows.append({**dict(p), "last_seen": s.get("last_seen"), "logins_30": s.get("logins_30", 0),
                     "exports_30": s.get("exports_30", 0), "views_30": s.get("views_30", 0)})
    return render_template("activity.html", active="activity", events=events, total=total, page=page, per_page=PAGE,
                           group=group, groups=activity.GROUPS, person=uid, people=rows, stats=stats,
                           labels=activity.ACTIONS, screens=activity.SCREENS)


@app.get("/admin/activity.csv")
@admin_only
def admin_activity_csv():
    import csv
    import io
    group = request.args.get("type", "")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "email", "role", "action", "detail"])
    for r in activity.csv_rows(group if group in dict(activity.GROUPS) else "", request.args.get("person", type=int)):
        w.writerow([datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"), r["email"], r["role"],
                    activity.ACTIONS.get(r["action"], (r["action"],))[0], r["detail"]])
    log("export", "activity log")
    return app.response_class(out.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=heritageedge-activity.csv"})


def _alias_view():
    """Aliases as (cluster as typed, chain) pairs for display."""
    names = {access.norm(c): c for c in users.all_scope_clusters()}
    chains = {access.norm(c): c for c in access.chain_names()}
    return [(names.get(k, k), chains.get(v, v), k) for k, v in sorted(users.aliases().items())]


def _done(text, link=None):
    session["people_msg"] = {"text": text, "link": link}
    return redirect(url_for("admin_users"))


@app.post("/admin/users/new")
@admin_only
def admin_users_new():
    try:
        role = request.form.get("role", "kam")
        if role not in ("kam", "leadership"):
            role = "kam"
        uid = users.create(request.form.get("email", ""), request.form.get("name", ""), role,
                           request.form.get("employee_id", ""))
    except users.UserError as exc:
        return _done(str(exc))
    user = users.get(uid)
    log("user_created", f"{user['email']} as {users.ROLE_NAMES.get(role, role)}")
    sent, link = send_link(user, "invite")
    if sent:
        return _done(f"Account created. A link to set a password was emailed to {user['email']}.")
    return _done(f"Account created, but the email could not be sent. Send {user['email']} this link yourself "
                 f"(it works once and expires in {users.INVITE_HOURS} hours):", link)


@app.post("/admin/users/upload")
@admin_only
def admin_users_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return _done("Choose the KAM Excel file first.")
    try:
        report = access.import_mapping(file, request.form.get("starting_password", ""))
    except users.UserError as exc:
        return _done(str(exc))
    log("mapping_uploaded", str(report).splitlines()[0] if report else "")
    return _done(report)


@app.get("/admin/users/<int:uid>")
@admin_only
def admin_user_detail(uid):
    user = users.get(uid)
    if not user:
        return redirect(url_for("admin_users"))
    matched = {access.norm(c) for c in chains_in_data()}
    alias = users.aliases()
    scope = users.scope_rows(uid)
    for sc in scope:
        cn = access.norm(sc["cluster"])
        sc["matched"] = alias.get(cn, cn) in matched
    return render_template("user_detail.html", active="users", person=user, scope=scope, states=access.state_names(),
                           chains=access.chain_names(), message=session.pop("people_msg", None))


def chains_in_data():
    return access.chain_names()


def _back(uid, text):
    session["people_msg"] = {"text": text, "link": None}
    return redirect(url_for("admin_user_detail", uid=uid))


@app.post("/admin/users/<int:uid>/details")
@admin_only
def admin_user_details(uid):
    try:
        users.update_details(uid, request.form.get("name", ""), request.form.get("employee_id", ""))
        role = request.form.get("role")
        if role:
            users.set_role(uid, role)
    except users.UserError as exc:
        return _back(uid, str(exc))
    log("role_changed", f"{_target(uid)}, role {role or 'unchanged'}")
    return _back(uid, "Saved.")


@app.post("/admin/users/<int:uid>/scope/add")
@admin_only
def admin_user_scope_add(uid):
    try:
        users.add_scope(uid, request.form.get("state", ""), request.form.get("cluster", ""))
    except users.UserError as exc:
        return _back(uid, str(exc))
    log("mapping_added", f"{_target(uid)}: {request.form.get('state', '')} / {request.form.get('cluster', '')}")
    return _back(uid, "Mapping added.")


@app.post("/admin/users/<int:uid>/scope/<int:sid>/remove")
@admin_only
def admin_user_scope_remove(uid, sid):
    users.remove_scope(uid, sid)
    log("mapping_removed", _target(uid))
    return _back(uid, "Mapping removed.")


@app.post("/admin/aliases")
@admin_only
def admin_alias():
    cluster = access.norm(request.form.get("cluster", ""))
    chain = access.norm(request.form.get("chain", ""))
    if not cluster:
        return _done("Choose a cluster.")
    users.set_alias(cluster, chain)
    log("alias_changed", f"{request.form.get('cluster', '')} to {request.form.get('chain', '') or 'nothing'}")
    return _done("Alias saved." if chain else "Alias removed.")


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not g.user:
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        if request.form.get("password") != request.form.get("again"):
            error = "The two new passwords are not the same."
        else:
            try:
                users.change_password(g.user["id"], request.form.get("current", ""), request.form.get("password", ""))
            except users.UserError as exc:
                error = str(exc)
            else:
                log("password_changed")
                return redirect(url_for("simulator_page"))
    return render_template("change_password.html", error=error, forced=bool(g.user["must_change"]),
                           min_length=users.MIN_PASSWORD)


@app.post("/admin/users/<int:uid>/active")
@admin_only
def admin_users_active(uid):
    on = request.form.get("active") == "1"
    try:
        users.set_active(uid, on)
    except users.UserError as exc:
        return _done(str(exc))
    user = users.get(uid)
    log("user_enabled" if on else "user_disabled", user["email"])
    return _done(f"{user['email']} is now {'enabled' if on else 'disabled'}.")


@app.post("/admin/users/<int:uid>/link")
@admin_only
def admin_users_link(uid):
    user = users.get(uid)
    if not user or not user["active"]:
        return _done("Enable the account first.")
    sent, link = send_link(user, "invite")
    log("link_sent", user["email"])
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
            "csrf_token": csrf_token, "role_names": users.ROLE_NAMES, "limited": access.limited(g.get("user")),
            "network_svg": lambda: network.svg(network.labels(), url_for("static", filename="img/heritage-logo.png"))}


@app.template_filter("datetime")
def _datetime(ts):
    return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M")


@app.template_filter("day")
def _day(ts):
    return datetime.fromtimestamp(ts).strftime("%d %b")


@app.template_filter("ago")
def _ago(ts):
    if not ts:
        return "Never"
    secs = max(time.time() - ts, 0)
    if secs < 90:
        return "Just now"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size:
            n = int(secs // size)
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "Just now"


# ---------- pages ----------
@app.get("/")
def simulator_page():
    return _no_accounts("simulator") or render_template("simulator.html", active="simulator")


@app.get("/pnl")
def pnl_page():
    return _no_accounts("pnl") or render_template("pnl.html", active="pnl")


@app.get("/data")
@data_admin_only
def data_page():
    return render_template("data.html", active="data")


# ---------- numbers ----------
@app.get("/api/meta")
def meta():
    allowed, limited = visible()
    options, combos = access.options_for(allowed)
    return jsonify({
        "status": data.status() if not limited else {"mode": data.MODE, "title": data.status()["title"]},
        "dims": [{"id": d, "name": name, "options": options[d]} for d, name in data.DIM_DEFS],
        "combos": combos,
        "user": {"role": g.user["role"] if g.get("user") else None, "limited": limited},
    })


def _payload():
    body = request.get_json(silent=True) or {}
    return body.get("levers"), body.get("scope"), body.get("dims"), body.get("filters")


@app.post("/api/simulate")
def api_simulate():
    levers, scope, _, _ = _payload()
    allowed, limited = visible()
    out = simulate.simulate(levers, scope, allowed)
    return jsonify(simulate.strip_below_cm(out) if limited else out)


@app.post("/api/pnl")
def api_pnl():
    levers, scope, dims, flt = _payload()
    allowed, limited = visible()
    out = simulate.pnl(levers, scope, dims, flt, allowed)
    return jsonify(simulate.strip_below_cm(out) if limited else out)


@app.post("/api/export")
def api_export():
    levers, scope, dims, flt = _payload()
    allowed, limited = visible()
    buf = export.build_workbook(levers, scope, dims, flt, allowed, limited)
    log("export", "to contribution only" if limited else "full P&L")
    return send_file(buf, as_attachment=True, download_name="heritageedge-pnl.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- data page ----------
@app.get("/api/data/status")
@data_admin_only
def data_status():
    return jsonify(datastore.status())


@app.post("/api/data/upload/<kind>")
@data_admin_only
def data_upload(kind):
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify(error="Choose a file to upload."), 400
    try:
        info = datastore.save_upload(kind, file)
    except prepare.DataError as exc:
        return jsonify(error=str(exc)), 400
    log("data_upload", kind)
    return jsonify(info)


@app.post("/api/data/sql/test")
@data_admin_only
def data_sql_test():
    ok, message = sql_source.test_connection()
    return jsonify(ok=ok, message=message)


@app.post("/api/data/sql/pull")
@data_admin_only
def data_sql_pull():
    start = (request.get_json(silent=True) or {}).get("start") or sql_source.default_start().isoformat()
    if not datastore.pull_billing(start):
        return jsonify(error="Another step is still running."), 409
    log("data_pull", f"from {start}")
    return jsonify(started=True)


@app.post("/api/data/build")
@data_admin_only
def data_build():
    months = (request.get_json(silent=True) or {}).get("months") or []
    if not datastore.build(months):
        return jsonify(error="Another step is still running."), 409
    log("data_build", f"{len(months)} base month{'' if len(months) == 1 else 's'}")
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
