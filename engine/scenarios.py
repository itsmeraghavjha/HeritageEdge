"""Saved scenarios, kept on the server so they follow a person across browsers and can be seen on the Scenarios screen.

Stored in data/users.db next to the accounts (git-ignored). A scenario is the lever values, the selection (chains,
states, SKUs) and the row adjustments, never any computed figure: results are worked out fresh from today's data.

Who sees what: leadership and admin see every scenario; a KAM sees only their own. Only the person who saved a
scenario (or an admin) can rename or delete it; anyone who can see one can open it and save their own copy.
"""
import json
import sqlite3
import time
from contextlib import closing

from . import data, simulate

MAX_NAME = 80
MAX_PER_PERSON = 300
MAX_BYTES = 8_000_000                # a scenario from the scenario sheet holds one small entry per changed row
MAX_NOTE = 1000


class ScenarioError(Exception):
    """A problem the person can fix; the message is safe to show."""


def _db():
    path = data.DATA_DIR / "users.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS scenarios (
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL DEFAULT 0, owner_email TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL, levers TEXT NOT NULL, scope TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, UNIQUE (owner_id, name));
    """)
    have = {r["name"] for r in con.execute("PRAGMA table_info(scenarios)")}
    for column in ("note", "period"):                      # added later: older databases get them here
        if column not in have:
            con.execute(f"ALTER TABLE scenarios ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    return con


def _owner(user):
    """No accounts at all (a one-person local run): everything belongs to owner 0 and everyone is that person."""
    return (user["id"], user["email"]) if user else (0, "")


def _sees_all(user):
    return user is None or user["role"] in ("admin", "leadership")


def can_see(user, row):
    return _sees_all(user) or row["owner_id"] == user["id"]


def can_change(user, row):
    return user is None or user["role"] == "admin" or row["owner_id"] == user["id"]


# ---------- cleaning what the browser sends ----------
def clean_name(name):
    name = " ".join(str(name or "").split())
    if not name:
        raise ScenarioError("Give the scenario a name.")
    if len(name) > MAX_NAME:
        raise ScenarioError(f"Keep the name to {MAX_NAME} characters or fewer.")
    return name


def clean_note(note):
    note = "\n".join(line.rstrip() for line in str(note or "").strip().splitlines())
    if len(note) > MAX_NOTE:
        raise ScenarioError(f"Keep the purpose to {MAX_NOTE} characters or fewer.")
    return note


def clean_levers(raw):
    """Lever values clamped to their limits, plus the row adjustments, in the shape the simulator sends."""
    lv, adjust = simulate.split_levers(raw)
    out = {k: v for k, v in lv.__dict__.items()}
    if adjust:
        out["adjust"] = adjust
    return out


def clean_scope(raw):
    out = {}
    if isinstance(raw, dict):
        for dim, ids in raw.items():
            if dim in {d for d, _ in data.DIM_DEFS} and isinstance(ids, (list, tuple)) and ids:
                out[dim] = [str(i) for i in ids][:2000]
    return out


# ---------- storing ----------
def _shape(row):
    return {"id": row["id"], "name": row["name"], "owner_id": row["owner_id"], "owner": row["owner_email"],
            "created": row["created"], "updated": row["updated"], "note": row["note"], "period": row["period"],
            "levers": json.loads(row["levers"]), "scope": json.loads(row["scope"])}


def _unique_name(con, owner_id, name, ignore_id=None):
    taken = {r["name"].lower() for r in con.execute("SELECT id, name FROM scenarios WHERE owner_id=?", (owner_id,))
             if r["id"] != ignore_id}
    if name.lower() not in taken:
        return name
    base, n = name, 2
    candidate = f"{base} ({n})"
    while candidate.lower() in taken:
        n += 1
        candidate = f"{base} ({n})"
    return candidate


def save(user, name, levers, scope, scenario_id=None, copy=False, note=None, period=None):
    """Saves a scenario and returns its id. With an id the person may change, that scenario is updated; otherwise a
    scenario with the same name of the same person is replaced, else a new one is added. With `copy` it is always a new
    scenario (a number is added to the name if the person already has one by that name)."""
    name = clean_name(name)
    lv, sc = clean_levers(levers), clean_scope(scope)
    note = None if note is None else clean_note(note)       # None keeps what an updated scenario already has
    period = period if period in data.period_keys() else ""
    if len(json.dumps(lv)) + len(json.dumps(sc)) > MAX_BYTES:
        raise ScenarioError("This scenario is too large to save.")
    owner_id, owner_email = _owner(user)
    now = time.time()
    with closing(_db()) as con, con:
        existing = None
        if copy:
            name = _unique_name(con, owner_id, name)
        elif scenario_id:
            row = con.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
            if row and can_see(user, row) and can_change(user, row):
                existing = row
        if existing is None and not copy:
            existing = con.execute("SELECT * FROM scenarios WHERE owner_id=? AND name=? COLLATE NOCASE",
                                   (owner_id, name)).fetchone()
        if existing:
            con.execute("UPDATE scenarios SET name=?, levers=?, scope=?, updated=?, note=?, period=? WHERE id=?",
                        (_unique_name(con, existing["owner_id"], name, existing["id"]), json.dumps(lv), json.dumps(sc),
                         now, existing["note"] if note is None else note, period or existing["period"], existing["id"]))
            return existing["id"]
        count = con.execute("SELECT COUNT(*) FROM scenarios WHERE owner_id=?", (owner_id,)).fetchone()[0]
        if count >= MAX_PER_PERSON:
            raise ScenarioError(f"You have {MAX_PER_PERSON} saved scenarios. Delete some before saving more.")
        cur = con.execute("INSERT INTO scenarios (owner_id, owner_email, name, levers, scope, created, updated, note, period) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (owner_id, owner_email, name, json.dumps(lv), json.dumps(sc), now, now, note or "", period))
        return cur.lastrowid


def get(user, scenario_id):
    """The scenario, or None when it does not exist or this person may not see it (the two look the same)."""
    with closing(_db()) as con:
        row = con.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
    return _shape(row) if row and can_see(user, row) else None


def listing(user, mine_only=False):
    owner_id = _owner(user)[0]
    with closing(_db()) as con:
        rows = con.execute("SELECT * FROM scenarios ORDER BY updated DESC").fetchall()
    out = []
    for r in rows:
        if not can_see(user, r) or (mine_only and r["owner_id"] != owner_id):
            continue
        s = _shape(r)
        s["mine"] = r["owner_id"] == owner_id
        s["can_change"] = can_change(user, r)
        out.append(s)
    return out


def rename(user, scenario_id, name):
    name = clean_name(name)
    with closing(_db()) as con, con:
        row = con.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        if not row or not can_see(user, row):
            raise ScenarioError("That scenario was not found.")
        if not can_change(user, row):
            raise ScenarioError("Only the person who saved a scenario can rename it.")
        clash = con.execute("SELECT 1 FROM scenarios WHERE owner_id=? AND name=? COLLATE NOCASE AND id<>?",
                            (row["owner_id"], name, scenario_id)).fetchone()
        if clash:
            raise ScenarioError("There is already a scenario with that name.")
        con.execute("UPDATE scenarios SET name=?, updated=? WHERE id=?", (name, time.time(), scenario_id))


def duplicate(user, scenario_id):
    """A copy owned by the person asking, so a shared scenario can be tried without touching the original."""
    src = get(user, scenario_id)
    if not src:
        raise ScenarioError("That scenario was not found.")
    owner_id, owner_email = _owner(user)
    with closing(_db()) as con:
        name = _unique_name(con, owner_id, clean_name(f"{src['name']} (copy)")[:MAX_NAME])
    return save(user, name, src["levers"], src["scope"], note=src["note"], period=src["period"])


def delete(user, scenario_id):
    with closing(_db()) as con, con:
        row = con.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        if not row or not can_see(user, row):
            raise ScenarioError("That scenario was not found.")
        if not can_change(user, row):
            raise ScenarioError("Only the person who saved a scenario can delete it.")
        con.execute("DELETE FROM scenarios WHERE id=?", (scenario_id,))


# ---------- results, worked out fresh from today's data ----------
def results(scn, allowed, limited):
    """Headline figures for one scenario on the rows this viewer may see. A KAM's figures stop at contribution."""
    out = simulate.headline(scn["levers"], scn["scope"], allowed)
    b, s = out["base"], out["sim"]
    res = {"rows": out["rows_in_scope"], "net": s["net"], "d_net": s["net"] - b["net"], "cm": s["cm"],
           "d_cm": s["cm"] - b["cm"], "cm_pct": s["cm_pct"], "d_cm_pct": s["cm_pct"] - b["cm_pct"],
           "litres": s["litres"], "kg": s["kg"], "d_litres": s["litres"] - b["litres"], "d_kg": s["kg"] - b["kg"]}
    if not limited:
        res.update({"nm": s["nm"], "d_nm": s["nm"] - b["nm"]})
    return res


# ---------- the summary for management ----------
COST_LINES = frozenset({"promo", "rebates", "cogs", "ou", "logistics", "pa", "interest", "mktg"})   # a rise is bad
SUMMARY_DIMS = ("chain", "state", "family", "sku")
TOP = 8


def _vol_l(m):
    return m["litres"] + m["kg"]                        # kg counts as litres, as in the LPD figure


def summary(scn, allowed, limited):
    """What a saved scenario changes and what it does to the P&L, on the rows this viewer may see, worked out fresh from
    the data in use. Meant to be read by someone who did not make the scenario: the levers and rows changed first, then the
    P&L baseline against scenario, what moves contribution, where it lands, and the rows that move most.
    Rupee amounts are rupees; the screen shows them in lakh. A KAM's figures stop at contribution."""
    from . import scenario_sheet as sheet
    lv, adjust = simulate.split_levers(scn["levers"])
    scope = simulate.parse_scope(scn["scope"])
    rows, base = simulate.view_rows(scope, allowed)
    sims = simulate.scenario_metrics(lv, scope, rows, adjust)
    resolve = simulate.make_resolver(lv, adjust)
    b, s = simulate.derive(simulate.total(base)), simulate.derive(simulate.total(sims))
    b["vol_l"], s["vol_l"] = _vol_l(b), _vol_l(s)
    days = data.days_per_month()

    def hide(key):
        return limited and key in simulate.BELOW_CONTRIBUTION

    # the P&L, baseline against scenario
    lines = []
    for title, key, scale, _ in sheet._pl_columns(limited):
        kind = "pct" if key.endswith("_pct") else ("lpd" if key == "vol_l" else "money")
        div = days if key == "vol_l" else 1
        lines.append({"label": title, "key": key, "kind": kind, "base": b[key] / div, "sim": s[key] / div,
                      "cost": key in COST_LINES})

    # what was changed: global levers, then how many rows each lever reaches and how far it moves them
    from .export import LEVER_LABELS
    levers = [{"label": LEVER_LABELS[k][0], "unit": LEVER_LABELS[k][1], "value": getattr(lv, k)}
              for k in LEVER_LABELS if getattr(lv, k)]
    changed, reach = [], {k: [] for k in simulate.ADJUST_LEVERS}
    for i, row in enumerate(rows):
        eff = resolve(row)
        moved = {k: getattr(eff, k) - getattr(lv, k) for k in simulate.ADJUST_LEVERS if abs(getattr(eff, k) - getattr(lv, k)) > 1e-9}
        if moved:
            changed.append(i)
            for k, v in moved.items():
                reach[k].append(v)
    row_levers = [{"label": LEVER_LABELS[k][0], "unit": LEVER_LABELS[k][1], "rows": len(v), "low": min(v), "high": max(v)}
                  for k, v in reach.items() if v]
    scope_text = [{"dimension": dict(data.DIM_DEFS).get(d, d), "names": [data.LABEL[d].get(i, i) for i in ids]}
                  for d, ids in scn["scope"].items() if ids]

    # where it lands
    def group(dim):
        by = {}
        for i, (row, bm, sm) in enumerate(zip(rows, base, sims)):
            g = by.setdefault(str(row[dim]), {"b": [], "s": [], "n": 0})
            g["b"].append(bm)
            g["s"].append(sm)
            g["n"] += 1 if i in set_changed else 0
        out = []
        for key, g in by.items():
            gb, gs = simulate.derive(simulate.total(g["b"])), simulate.derive(simulate.total(g["s"]))
            if g["n"] == 0 and abs(gs["cm"] - gb["cm"]) < 1 and abs(gs["net"] - gb["net"]) < 1:
                continue
            out.append({"name": data.LABEL[dim].get(key, key), "rows_changed": g["n"],
                        "d_vol": (_vol_l(gs) - _vol_l(gb)) / days, "d_net": gs["net"] - gb["net"],
                        "d_cm": gs["cm"] - gb["cm"], "cm_pct": gs["cm_pct"], "d_cm_pct": gs["cm_pct"] - gb["cm_pct"]})
        out.sort(key=lambda x: -abs(x["d_cm"]))
        return {"dimension": dict(data.DIM_DEFS).get(dim, dim), "shown": min(TOP, len(out)), "total": len(out), "items": out[:TOP]}

    set_changed = set(changed)
    where = [group(d) for d in SUMMARY_DIMS]

    # the rows that move most, with the numbers they were given
    top = []
    for i in sorted(changed, key=lambda i: -abs(sims[i]["cm"] - base[i]["cm"]))[:15]:
        row = rows[i]
        t0, t1 = sheet._terms(row, simulate.DEFAULT_LEVERS), sheet._terms(row, resolve(row))
        steps = []
        if t0:
            for k, title, _ in sheet.DRIVERS:
                if abs(t1[k] - t0[k]) > 1e-7:
                    steps.append({"label": title, "base": t0[k], "scenario": t1[k]})
        top.append({"name": " / ".join(str(data.LABEL[d].get(row[d], row[d])) for d in ("chain", "state", "sku")),
                    "changes": steps, "d_cm": sims[i]["cm"] - base[i]["cm"], "d_net": sims[i]["net"] - base[i]["net"]})

    out = {"lines": lines, "levers": levers, "row_levers": row_levers, "scope": scope_text, "where": where, "top_rows": top,
           "bridge": simulate.bridge(lv, scope, sims, rows, base, adjust),
           "rows_in_scope": len(rows), "rows_changed": len(changed), "rows_total": len(simulate._visible(allowed)),
           "limited": limited, "period": data.period_label(data.META) if data.MODE == "actual" else "Sample data, not actuals"}
    return out
