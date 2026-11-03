"""User accounts: email + own password, set through a link, with one admin who creates and disables users.

Stored in data/users.db (SQLite). data/ is ignored by git, so accounts never reach GitHub.
Passwords are stored only as salted hashes. Set-password links hold a random secret; only its hash is stored,
each link works once and expires, and a newer link for the same person cancels the older ones.
"""
import hashlib
import re
import secrets
import sqlite3
import time
from contextlib import closing

from werkzeug.security import check_password_hash, generate_password_hash

from . import data

INVITE_HOURS = 72          # a link for a new user
RESET_HOURS = 2            # a link from "Forgot password"
MIN_PASSWORD = 10
MAX_FAILS = 5              # wrong passwords allowed ...
LOCK_MINUTES = 15          # ... in this many minutes before the account waits

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = generate_password_hash("not-a-real-password")   # so a missing email takes as long as a wrong password


class UserError(Exception):
    """A problem the person can fix; the message is safe to show."""


def _db():
    path = data.DATA_DIR / "users.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1,
            pw_hash TEXT, created REAL NOT NULL, last_login REAL);
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, hash TEXT UNIQUE NOT NULL,
            purpose TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fails (email TEXT NOT NULL, at REAL NOT NULL);
    """)
    return con


def clean_email(value):
    email = (value or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 200:
        raise UserError("Enter a valid email address.")
    return email


def check_password(password, email=""):
    if len(password or "") < MIN_PASSWORD:
        raise UserError(f"Use at least {MIN_PASSWORD} characters.")
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise UserError("Use both letters and numbers.")
    if email and password.strip().lower() == email.lower():
        raise UserError("The password cannot be your email address.")


def count():
    with closing(_db()) as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def has_users():
    return count() > 0


def get(user_id):
    with closing(_db()) as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_by_email(email):
    with closing(_db()) as con:
        row = con.execute("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def list_users():
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM users ORDER BY role='admin' DESC, lower(email)")]


def create(email, name="", role="user"):
    """Adds a person with no password yet. There is only ever one admin."""
    email = clean_email(email)
    if role not in ("admin", "user"):
        raise UserError("Unknown role.")
    with closing(_db()) as con:
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise UserError("That email already has an account.")
        if role == "admin" and con.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            raise UserError("There is already an admin.")
        cur = con.execute("INSERT INTO users (email, name, role, created) VALUES (?,?,?,?)",
                          (email, (name or "").strip()[:100], role, time.time()))
        con.commit()
        return cur.lastrowid


def set_active(user_id, active):
    with closing(_db()) as con:
        row = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise UserError("No such user.")
        if row["role"] == "admin" and not active:
            raise UserError("The admin cannot be disabled.")
        con.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
        con.commit()


# ---------- links ----------
def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(user_id, purpose):
    """Returns the secret to put in the link. Earlier unused links for this person stop working."""
    hours = INVITE_HOURS if purpose == "invite" else RESET_HOURS
    token = secrets.token_urlsafe(32)
    with closing(_db()) as con:
        con.execute("UPDATE tokens SET used=1 WHERE user_id=?", (user_id,))
        con.execute("INSERT INTO tokens (user_id, hash, purpose, expires) VALUES (?,?,?,?)",
                    (user_id, _hash(token), purpose, time.time() + hours * 3600))
        con.commit()
    return token


def token_user(token):
    """The person a link belongs to, or None if it is unknown, used, expired, or the account is disabled."""
    if not token:
        return None
    with closing(_db()) as con:
        row = con.execute("SELECT t.id AS tid, u.* FROM tokens t JOIN users u ON u.id=t.user_id "
                          "WHERE t.hash=? AND t.used=0 AND t.expires>? AND u.active=1",
                          (_hash(token), time.time())).fetchone()
    return dict(row) if row else None


def set_password_with_token(token, password):
    user = token_user(token)
    if not user:
        raise UserError("This link has expired or was already used. Ask for a new one.")
    check_password(password, user["email"])
    with closing(_db()) as con:
        con.execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(password), user["id"]))
        con.execute("UPDATE tokens SET used=1 WHERE user_id=?", (user["id"],))
        con.execute("DELETE FROM fails WHERE email=?", (user["email"],))
        con.commit()
    return user


# ---------- login ----------
def _recent_fails(con, email):
    since = time.time() - LOCK_MINUTES * 60
    return con.execute("SELECT COUNT(*) FROM fails WHERE email=? AND at>?", (email, since)).fetchone()[0]


def login(email, password):
    """Returns the user, or raises UserError with a message that does not reveal whether the email exists."""
    email = (email or "").strip().lower()
    wrong = UserError("The email or password is not right.")
    with closing(_db()) as con:
        if _recent_fails(con, email) >= MAX_FAILS:
            raise UserError(f"Too many wrong attempts. Try again in {LOCK_MINUTES} minutes.")
        row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        ok = check_password_hash(row["pw_hash"] if row and row["pw_hash"] else _DUMMY_HASH, password or "")
        if not (row and row["pw_hash"] and ok):
            con.execute("INSERT INTO fails (email, at) VALUES (?,?)", (email, time.time()))
            con.commit()
            raise wrong
        if not row["active"]:
            raise UserError("This account is disabled. Ask the admin.")
        con.execute("DELETE FROM fails WHERE email=?", (email,))
        con.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
        con.commit()
        return dict(row)
