"""Operator accounts: the people allowed to sign in and use the application.

Stored in the `auth_accounts` table (not the app's `users` table, which is
domain data the agent manages). Passwords are scrypt hashes via `auth`, so a
plaintext password never touches the database. A token salt is derived from the
stored hash, so changing a password invalidates that user's existing sessions.
"""

import re
import threading
from datetime import datetime, timezone
from typing import List, Optional

from . import auth
from .db.database import get_connection, init_db, row_to_dict

MIN_USERNAME = 3
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

# Serialises the check-then-insert in create(): SQLite's UNIQUE constraint is the
# real guard, but this turns a race into a clean "username taken" message.
_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_new(username: str, password: str, email: Optional[str] = None) -> Optional[str]:
    """Return an error message if the details are unacceptable, else None."""
    username = (username or "").strip()
    if len(username) < MIN_USERNAME:
        return f"Username must be at least {MIN_USERNAME} characters."
    if not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return "Username may contain only letters, numbers, and . _ -"
    if len(password or "") < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if email and not EMAIL_RE.match(email.strip()):
        return f"'{email}' is not a valid email address."
    return None


def count() -> int:
    init_db()
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM auth_accounts").fetchone()[0]


def get(username: str) -> Optional[dict]:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM auth_accounts WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
    return row_to_dict(row) if row else None


def get_by_email(email: str) -> Optional[dict]:
    if not email:
        return None
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM auth_accounts WHERE email = ? COLLATE NOCASE",
            (email.strip(),),
        ).fetchone()
    return row_to_dict(row) if row else None


def list_accounts() -> List[dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, email, is_admin, created_at FROM auth_accounts ORDER BY id"
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def create(username: str, password: str, is_admin: bool = False, email: Optional[str] = None) -> dict:
    """Create an account. Returns {status: success|error, ...}."""
    init_db()
    username = (username or "").strip()
    email = (email or "").strip() or None
    error = validate_new(username, password, email)
    if error:
        return {"status": "error", "message": error}

    with _guard:
        if get(username):
            return {"status": "error", "message": f"The username '{username}' is taken."}
        if email and get_by_email(email):
            return {"status": "error", "message": f"That email is already registered."}
        ts = _now()
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO auth_accounts (username, password_hash, email, is_admin, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (username, auth.hash_password(password), email, 1 if is_admin else 0, ts, ts),
            )
            account_id = cur.lastrowid
    return {
        "status": "success",
        "account": {"id": account_id, "username": username, "email": email, "is_admin": is_admin},
    }


def set_email(username: str, email: Optional[str]) -> dict:
    email = (email or "").strip() or None
    if email and not EMAIL_RE.match(email):
        return {"status": "error", "message": f"'{email}' is not a valid email address."}
    account = get(username)
    if not account:
        return {"status": "error", "message": "Account not found."}
    if email:
        clash = get_by_email(email)
        if clash and clash["id"] != account["id"]:
            return {"status": "error", "message": "That email is already registered."}
    with get_connection() as conn:
        conn.execute(
            "UPDATE auth_accounts SET email = ?, updated_at = ? WHERE id = ?",
            (email, _now(), account["id"]),
        )
    return {"status": "success", "email": email}


def verify(username: str, password: str) -> Optional[dict]:
    """Return the account if the credentials are valid, else None.

    Runs the hash even when the user is unknown, so a missing username costs the
    same as a wrong password and cannot be discovered by timing.
    """
    account = get(username)
    stored = account["password_hash"] if account else auth.DUMMY_HASH
    ok = auth.verify_password(password, stored)
    return account if (account and ok) else None


def set_password(username: str, new_password: str) -> dict:
    if len(new_password or "") < MIN_PASSWORD:
        return {"status": "error", "message": f"Password must be at least {MIN_PASSWORD} characters."}
    account = get(username)
    if not account:
        return {"status": "error", "message": "Account not found."}
    with get_connection() as conn:
        conn.execute(
            "UPDATE auth_accounts SET password_hash = ?, updated_at = ? WHERE id = ?",
            (auth.hash_password(new_password), _now(), account["id"]),
        )
    return {"status": "success"}


def delete(username: str) -> dict:
    account = get(username)
    if not account:
        return {"status": "error", "message": "Account not found."}
    if account["is_admin"]:
        admins = [a for a in list_accounts() if a["is_admin"]]
        if len(admins) <= 1:
            return {"status": "error", "message": "Cannot delete the last administrator."}
    with get_connection() as conn:
        conn.execute("DELETE FROM auth_accounts WHERE id = ?", (account["id"],))
    return {"status": "success"}


def token_salt(account: dict) -> str:
    """A per-user signing component that changes when the password changes.

    Folding a slice of the stored hash into the token signature means a password
    change (which re-salts the hash) invalidates every token issued before it.
    """
    return (account.get("password_hash") or "")[:24]


def ensure_seed_from_env(
    username: str, password_hash: str, password: str, email: Optional[str] = None
) -> None:
    """Bootstrap the first admin from environment variables, once.

    Only runs when no accounts exist, so an operator upgrading from the old
    env-only auth keeps their existing login without any migration step. A
    recovery email may be seeded too so password reset works out of the box.
    """
    if count() > 0 or not username:
        return
    email = (email or "").strip() or None
    if password_hash:
        ts = _now()
        with _guard, get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO auth_accounts "
                "(username, password_hash, email, is_admin, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (username.strip(), password_hash, email, ts, ts),
            )
    elif password:
        create(username, password, is_admin=True, email=email)
