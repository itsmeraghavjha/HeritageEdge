"""Who may see which rows, and the KAM mapping file.

A KAM sees the data rows whose state and chain match one of their mapping rows (state + cluster name). Names are
compared after dropping case, spaces and punctuation, so "V-Mart" matches "V-MART". A cluster whose name matches no
chain can be pointed at a chain by an alias the admin sets; it is never guessed. Admin and leadership see everything.
"""
import re

from . import data, users

HEADERS = {"state": "state", "cluster name": "cluster", "cluster": "cluster", "employee id": "employee_id",
           "employee name": "name", "email": "email"}


def norm(text):
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def limited(user):
    """True when the person is a KAM: scoped rows, and no P&L below contribution."""
    return bool(user) and user["role"] == "kam"


# ---------- which rows ----------
def _allowed_pairs(user_id):
    """(state, chain) pairs, normalised, that this KAM may see."""
    alias = users.aliases()
    chains_by_norm = {}
    for r in data.ROWS:
        chains_by_norm.setdefault(norm(r["chain"]), r["chain"])
    pairs = set()
    for sc in users.scope_rows(user_id):
        cn = norm(sc["cluster"])
        target = alias.get(cn, cn)
        if target in chains_by_norm:
            pairs.add((norm(sc["state"]), target))
    return pairs


def allowed_indices(user):
    """Positions in data.ROWS the person may see, or None for everything."""
    if not limited(user):
        return None
    pairs = _allowed_pairs(user["id"])
    return [i for i, r in enumerate(data.ROWS) if (norm(r["state"]), norm(r["chain"])) in pairs]


def options_for(indices):
    """The same shape as data.OPTIONS and data.COMBOS, cut to the allowed rows."""
    if indices is None:
        return data.OPTIONS, [list(c) for c in data.COMBOS]
    rows = [data.ROWS[i] for i in indices]
    options = {}
    for dim in data.DIM_IDS:
        seen = {}
        for r in rows:
            seen.setdefault(r[dim], data.LABEL[dim].get(r[dim], r[dim]))
        options[dim] = [{"id": k, "name": v} for k, v in sorted(seen.items(), key=lambda kv: str(kv[1]).lower())]
    combos = sorted({tuple(str(r[d]) for d in data.DIM_IDS) for r in rows})
    return options, [list(c) for c in combos]


def unmatched_clusters():
    """Cluster names in the mapping that match no chain in the data (and have no alias)."""
    alias = users.aliases()
    chains = {norm(r["chain"]) for r in data.ROWS}
    return [c for c in users.all_scope_clusters() if alias.get(norm(c), norm(c)) not in chains]


def chain_names():
    return sorted({r["chain"] for r in data.ROWS}, key=str.lower)


def state_names():
    return sorted({r["state"] for r in data.ROWS}, key=str.lower)


# ---------- the mapping file ----------
def parse_workbook(file):
    """Reads the KAM sheet. Returns (people, problems): people maps email to name, employee id and (state, cluster)
    pairs. The header row is found by its names, so extra columns (such as the joined state and cluster) are ignored."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(file, read_only=True, data_only=True)
    except Exception:
        raise users.UserError("That file could not be read. Upload an Excel (.xlsx) file.")
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    cols, problems, people = None, [], {}
    for n, row in enumerate(rows, start=1):
        if cols is None:
            found = {HEADERS[str(v).strip().lower()]: i for i, v in enumerate(row) if v and str(v).strip().lower() in HEADERS}
            if {"state", "cluster", "email"} <= set(found):
                cols = found
            elif n > 20:
                break
            continue
        get = lambda k: (str(row[cols[k]]).strip() if k in cols and cols[k] < len(row) and row[cols[k]] is not None else "")
        if not any(row):
            continue
        email = get("email").lower()
        try:
            email = users.clean_email(email)
        except users.UserError:
            problems.append(f"Row {n}: '{get('email') or 'no email'}' is not a valid email, skipped.")
            continue
        state, cluster = get("state"), get("cluster")
        if not state or not cluster:
            problems.append(f"Row {n}: state or cluster is empty, skipped.")
            continue
        who = people.setdefault(email, {"name": get("name"), "employee_id": get("employee_id").removesuffix(".0"),
                                        "pairs": []})
        if (state, cluster) not in who["pairs"]:
            who["pairs"].append((state, cluster))
        who["name"] = who["name"] or get("name")
    if cols is None:
        raise users.UserError("The header row was not found. The sheet needs State, Cluster Name, Employee ID, "
                              "Employee Name and Email columns.")
    return people, problems


def import_mapping(file, starting_password):
    """Creates the KAMs in the sheet with the starting password (they must change it at first login). People who
    already have an account keep their password and role; their name, employee id and mapping are updated.
    Returns a plain-text report."""
    users.check_password(starting_password, "")
    people, problems = parse_workbook(file)
    if not people:
        raise users.UserError("No KAM rows were found in the file.")
    created = updated = 0
    skipped_roles = []
    for email, p in people.items():
        existing = users.get_by_email(email)
        if existing is None:
            uid = users.create(email, p["name"], "kam", p["employee_id"], password=starting_password, must_change=True)
            created += 1
        else:
            uid = existing["id"]
            if existing["role"] != "kam":
                skipped_roles.append(email)
                continue
            users.update_details(uid, p["name"] or existing["name"], p["employee_id"] or existing["employee_id"])
            updated += 1
        users.replace_scope(uid, p["pairs"])
    lines = [f"{created} new KAM{'s' if created != 1 else ''} created, {updated} updated."]
    if skipped_roles:
        lines.append(f"Left alone because they are not KAMs: {', '.join(skipped_roles)}.")
    un = unmatched_clusters()
    if un:
        lines.append("No chain in the data matches these clusters, so those accounts show nothing yet: "
                     + ", ".join(un) + ". Set an alias below, or fix the name in the mapping.")
    return "\n".join(lines + problems[:8] + ([f"{len(problems) - 8} more problems not shown."] if len(problems) > 8 else []))
