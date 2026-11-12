"""Activity log: who signed in, who looked at what, who changed access and who exported.

Stored in data/users.db next to the accounts (git-ignored). It records events, never numbers: no lever values, no
prices, no chain figures, and never a password. Logging must never break the app, so every write swallows errors.
"""
import re
import sqlite3
import time
from contextlib import closing

from . import data

KEEP_DAYS = 400
DAY = 86400

# action -> (label, group). Groups drive the filters on the Activity page.
ACTIONS = {
    "login": ("Signed in", "access"),
    "login_failed": ("Failed sign-in", "access"),
    "logout": ("Signed out", "access"),
    "password_set": ("Set a password from a link", "access"),
    "password_changed": ("Changed password", "access"),
    "link_sent": ("Password link sent", "admin"),
    "user_created": ("Person added", "admin"),
    "user_enabled": ("Access enabled", "admin"),
    "user_disabled": ("Access disabled", "admin"),
    "role_changed": ("Details or role changed", "admin"),
    "mapping_added": ("Mapping added", "admin"),
    "mapping_removed": ("Mapping removed", "admin"),
    "mapping_uploaded": ("KAM file uploaded", "admin"),
    "alias_changed": ("Cluster matched to a chain", "admin"),
    "view": ("Opened a screen", "use"),
    "filter": ("Used a filter", "use"),
    "lever": ("Moved a lever", "use"),
    "export": ("Excel export", "export"),
    "scenario_saved": ("Saved a scenario", "use"),
    "scenario_deleted": ("Deleted a scenario", "use"),
    "data_upload": ("Data file uploaded", "data"),
    "data_pull": ("SQL volumes pulled", "data"),
    "data_build": ("Dataset built", "data"),
}
GROUPS = [("", "Everything"), ("access", "Sign-ins"), ("admin", "Access changes"), ("export", "Exports"),
          ("use", "Screens and filters"), ("data", "Data")]
LEVER_NAMES = {"price_change_pct": "MRP change", "promo_add_pp": "Extra promo discount",
               "trade_margin_add_pp": "Trade margin change", "rebate_add_pp": "Other deductions and display fees",
               "volume_change_pct": "Volume change", "cogs_change_pct": "COGS change",
               "freight_change_pct": "S&D / freight cost"}
SCREENS = {"simulator": "Promo simulator", "scenarios": "Scenarios", "pnl": "P&L statement", "data": "Data", "users": "Users", "activity": "Activity"}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_last_prune = [0.0]


def _db():
    path = data.DATA_DIR / "users.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY, ts REAL NOT NULL, user_id INTEGER, email TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS activity_ts ON activity (ts);
        CREATE INDEX IF NOT EXISTS activity_user ON activity (user_id, ts);
    """)
    return con


def record(user, action, detail="", email=None):
    """Adds one event. `user` is the acting person (a row/dict) or None; never raises."""
    try:
        if action not in ACTIONS:
            return
        who = email if email is not None else (user["email"] if user else "")
        if who and not _EMAIL.match(who):
            who = "(not an email address)"          # someone typed a password into the email box: do not keep it
        now = time.time()
        with closing(_db()) as con:
            con.execute("INSERT INTO activity (ts, user_id, email, role, action, detail) VALUES (?,?,?,?,?,?)",
                        (now, user["id"] if user else None, (who or "")[:120], user["role"] if user else "",
                         action, (detail or "")[:300]))
            if now - _last_prune[0] > DAY:
                _last_prune[0] = now
                con.execute("DELETE FROM activity WHERE ts < ?", (now - KEEP_DAYS * DAY,))
            con.commit()
    except Exception:
        pass


def _where(group="", user_id=None, since=None):
    sql, args = [], []
    if group:
        names = [a for a, (_, g) in ACTIONS.items() if g == group]
        sql.append("action IN (%s)" % ",".join("?" * len(names)))
        args += names
    if user_id:
        sql.append("user_id = ?")
        args.append(user_id)
    if since:
        sql.append("ts >= ?")
        args.append(since)
    return (" WHERE " + " AND ".join(sql)) if sql else "", args


def recent(limit=50, offset=0, group="", user_id=None):
    where, args = _where(group, user_id)
    with closing(_db()) as con:
        rows = con.execute(f"SELECT * FROM activity{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                           args + [limit, offset]).fetchall()
        total = con.execute(f"SELECT COUNT(*) FROM activity{where}", args).fetchone()[0]
    out = []
    for r in rows:
        label = ACTIONS.get(r["action"], (r["action"], ""))[0]
        out.append({**dict(r), "label": label, "group": ACTIONS.get(r["action"], ("", ""))[1]})
    return out, total


def summary(days=14, now=None):
    """Headline numbers, a per-day series and one line per person, from the log."""
    now = now or time.time()
    since7, since30 = now - 7 * DAY, now - 30 * DAY
    with closing(_db()) as con:
        def one(sql, *a):
            return con.execute(sql, a).fetchone()[0] or 0
        tiles = {
            "active_7": one("SELECT COUNT(DISTINCT user_id) FROM activity WHERE ts>=? AND user_id IS NOT NULL "
                            "AND action IN ('login','view','export')", since7),
            "logins_7": one("SELECT COUNT(*) FROM activity WHERE ts>=? AND action='login'", since7),
            "exports_7": one("SELECT COUNT(*) FROM activity WHERE ts>=? AND action='export'", since7),
            "failed_7": one("SELECT COUNT(*) FROM activity WHERE ts>=? AND action='login_failed'", since7),
        }
        # one bar per day (local day boundaries are close enough: the log keeps UTC timestamps)
        start = now - (days - 1) * DAY
        series = []
        for i in range(days):
            lo = int((start + i * DAY) // DAY) * DAY
            n = one("SELECT COUNT(DISTINCT user_id) FROM activity WHERE ts>=? AND ts<? AND user_id IS NOT NULL "
                    "AND action IN ('login','view','export')", lo, lo + DAY)
            logins = one("SELECT COUNT(*) FROM activity WHERE ts>=? AND ts<? AND action='login'", lo, lo + DAY)
            series.append({"day": lo, "people": n, "logins": logins})
        filters = [dict(r) for r in con.execute(
            "SELECT detail, COUNT(*) AS n, COUNT(DISTINCT user_id) AS people FROM activity "
            "WHERE action='filter' AND ts>=? GROUP BY detail ORDER BY n DESC", (since30,))]
        levers = [dict(r) for r in con.execute(
            "SELECT detail, COUNT(*) AS n, COUNT(DISTINCT user_id) AS people FROM activity "
            "WHERE action='lever' AND ts>=? GROUP BY detail ORDER BY n DESC", (since30,))]
        screens = [dict(r) for r in con.execute(
            "SELECT detail, COUNT(*) AS n FROM activity WHERE action='view' AND ts>=? GROUP BY detail "
            "ORDER BY n DESC", (since30,))]
        per = [dict(r) for r in con.execute("""
            SELECT user_id,
                   MAX(CASE WHEN action IN ('login','view','export') THEN ts END) AS last_seen,
                   SUM(CASE WHEN action='login' AND ts>=? THEN 1 ELSE 0 END) AS logins_30,
                   SUM(CASE WHEN action='export' AND ts>=? THEN 1 ELSE 0 END) AS exports_30,
                   SUM(CASE WHEN action='view' AND ts>=? THEN 1 ELSE 0 END) AS views_30
            FROM activity WHERE user_id IS NOT NULL GROUP BY user_id""", (since30, since30, since30))]
    peak = max([s["people"] for s in series] + [1])
    for s in series:
        s["pct"] = round(100 * s["people"] / peak)
    for s in screens:
        s["name"] = SCREENS.get(s["detail"], s["detail"])
    names = dict(data.DIM_DEFS)
    by_dim = {}
    for f in filters:                      # detail is "screen:dimension"; show one line per dimension across screens
        screen, _, dim = f["detail"].partition(":")
        row = by_dim.setdefault(dim, {"name": names.get(dim, dim), "n": 0, "people": 0, "where": {}})
        row["n"] += f["n"]
        row["people"] = max(row["people"], f["people"])
        row["where"][SCREENS.get(screen, screen)] = f["n"]
    filters = sorted(by_dim.values(), key=lambda r: -r["n"])
    return {"tiles": tiles, "series": series, "screens": screens, "filters": filters,
            "levers": [{"name": LEVER_NAMES.get(l["detail"], l["detail"]), "n": l["n"], "people": l["people"]}
                       for l in levers], "people": {p["user_id"]: p for p in per}}


def describe(action, detail):
    """Plain wording for the detail column."""
    if action == "view":
        return SCREENS.get(detail, detail)
    if action == "filter":
        screen, _, dim = detail.partition(":")
        return f"{dict(data.DIM_DEFS).get(dim, dim)}, on {SCREENS.get(screen, screen)}"
    if action == "lever":
        return LEVER_NAMES.get(detail, detail)
    return detail


def csv_rows(group="", user_id=None):
    where, args = _where(group, user_id)
    with closing(_db()) as con:
        return con.execute(f"SELECT ts, email, role, action, detail FROM activity{where} ORDER BY ts DESC", args).fetchall()
