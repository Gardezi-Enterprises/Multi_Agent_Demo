"""SQLite persistence for the user store.

Kept deliberately small: a single `users` table plus a connection helper. The
User Management Agent's tools are the only writers.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT,
    department  TEXT,
    skills      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Operator logins. Distinct from `users`, which is domain data the agent
-- manages; these are the people allowed to use the application at all.
CREATE TABLE IF NOT EXISTS auth_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    email         TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

# Additive column migrations for databases created before a column existed.
# (CREATE TABLE IF NOT EXISTS won't alter an existing table.)
MIGRATIONS = {
    "auth_accounts": {"email": "ALTER TABLE auth_accounts ADD COLUMN email TEXT"},
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(ddl)

EDITABLE_FIELDS = ("name", "email", "phone", "department", "skills")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    """Open a connection tuned for concurrent web requests.

    `timeout` makes writers wait for a held lock instead of raising
    "database is locked" immediately, which is the usual failure once more
    than one request is in flight.
    """
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_initialised = False
_init_guard = threading.Lock()


def init_db(force: bool = False) -> None:
    """Create the schema if needed. Safe to call from any thread, repeatedly."""
    global _initialised
    if _initialised and not force:
        return
    with _init_guard:
        if _initialised and not force:
            return
        with get_connection() as conn:
            # WAL lets readers proceed during a write — worth having as soon as
            # requests are served concurrently.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(SCHEMA)
            _apply_migrations(conn)
        _initialised = True


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def insert_user(
    name: str,
    email: str,
    phone: Optional[str] = None,
    department: Optional[str] = None,
    skills: Optional[str] = None,
) -> Dict[str, Any]:
    ts = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, email, phone, department, skills, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, email, phone, department, skills, ts, ts),
        )
        user_id = cur.lastrowid
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)


def select_users(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM users ORDER BY id"
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_dict(r) for r in rows]


def find_user(user_id: Optional[int] = None, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if user_id is not None:
        query, params = "SELECT * FROM users WHERE id = ?", (user_id,)
    elif email:
        query, params = "SELECT * FROM users WHERE lower(email) = lower(?)", (email,)
    else:
        return None
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return row_to_dict(row) if row else None


def update_user_fields(user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a validated field->value mapping to one user and return the new row."""
    updates = {k: v for k, v in updates.items() if k in EDITABLE_FIELDS and v is not None}
    assignments = ", ".join(f"{field} = ?" for field in updates)
    params = list(updates.values()) + [_now(), user_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?", params
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)
