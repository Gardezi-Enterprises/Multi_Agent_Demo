"""Database layer — SQLite (default) or PostgreSQL.

The whole app targets one small dialect-agnostic surface: `?` placeholders,
`RETURNING id` on inserts, and rows that behave like dicts. A thin connection
wrapper adapts that to either backend, so nothing above this file knows or cares
which database is in use.

SQLite needs zero configuration and is ideal for local dev. Set DATABASE_URL to a
`postgres://` URL (e.g. a free Neon or Supabase database) to run fully online
with no local file.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import DATABASE_URL, DB_PATH, IS_POSTGRES

# Case-insensitive uniqueness is enforced with expression indexes on lower(),
# which both engines support — portable, unlike SQLite's COLLATE NOCASE.
_COMMON_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id          {pk},
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT,
    department  TEXT,
    skills      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_accounts (
    id            {pk},
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    email         TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username ON auth_accounts (lower(username));
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT 'New chat',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations (lower(owner));
CREATE TABLE IF NOT EXISTS messages (
    id              {pk},
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    trace           TEXT,
    downloads       TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, id);
"""

SCHEMA_SQLITE = _COMMON_TABLES.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT")
SCHEMA_POSTGRES = _COMMON_TABLES.format(pk="SERIAL PRIMARY KEY")

EDITABLE_FIELDS = ("name", "email", "phone", "department", "skills")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Row(dict):
    """A dict row that also supports positional access (row[0]) for scalars."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Result:
    def __init__(self, rows: List[Row]):
        self._rows = rows

    def fetchone(self) -> Optional[Row]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Row]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


# A pooled Postgres connection is reused across requests, avoiding a fresh TLS
# handshake per query — a big win when the database is a hosted Postgres some
# distance away (e.g. Neon). SQLite opens a local file, so it needs no pool.
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _pool():
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                from psycopg_pool import ConnectionPool

                _pg_pool = ConnectionPool(
                    DATABASE_URL, min_size=1, max_size=8, timeout=30, open=True
                )
    return _pg_pool


class Connection:
    """Uniform connection over sqlite3 or pooled psycopg, as a context manager.

    The connection is acquired on `__enter__` (borrowed from the pool for
    Postgres) and released on `__exit__`, committing on success and rolling back
    on error.
    """

    _raw = None
    _pool_ctx = None

    def __enter__(self) -> "Connection":
        if IS_POSTGRES:
            self._pool_ctx = _pool().connection()
            self._raw = self._pool_ctx.__enter__()
        else:
            import sqlite3

            self._raw = sqlite3.connect(DB_PATH, timeout=15.0)
            self._raw.row_factory = sqlite3.Row
            self._raw.execute("PRAGMA foreign_keys = ON")
        return self

    def execute(self, sql: str, params: tuple = ()) -> _Result:
        if IS_POSTGRES:
            cur = self._raw.cursor()
            cur.execute(sql.replace("?", "%s"), params)
            rows = (
                [Row(zip((c.name for c in cur.description), r)) for r in cur.fetchall()]
                if cur.description
                else []
            )
            cur.close()
            return _Result(rows)
        cur = self._raw.execute(sql, params)
        rows = [Row(dict(r)) for r in cur.fetchall()] if cur.description else []
        return _Result(rows)

    def executescript(self, script: str) -> None:
        if IS_POSTGRES:
            with self._raw.cursor() as cur:
                for statement in script.split(";"):
                    if statement.strip():
                        cur.execute(statement)
        else:
            self._raw.executescript(script)

    def __exit__(self, exc_type, exc, tb) -> None:
        if IS_POSTGRES:
            # The pool's context commits/rolls back and returns the connection.
            self._pool_ctx.__exit__(exc_type, exc, tb)
        else:
            try:
                if exc_type:
                    self._raw.rollback()
                else:
                    self._raw.commit()
            finally:
                self._raw.close()


def get_connection() -> Connection:
    return Connection()


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
            if IS_POSTGRES:
                conn.executescript(SCHEMA_POSTGRES)
            else:
                # WAL keeps readers unblocked during writes under concurrency.
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                conn.executescript(SCHEMA_SQLITE)
                _migrate_sqlite(conn)
        _initialised = True


def _migrate_sqlite(conn: Connection) -> None:
    """Add columns to pre-existing SQLite databases (Postgres starts current)."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(auth_accounts)")}
    if "email" not in existing:
        conn.execute("ALTER TABLE auth_accounts ADD COLUMN email TEXT")


def row_to_dict(row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


# --- user store (User Management Agent's tools) ------------------------------


def insert_user(
    name: str,
    email: str,
    phone: Optional[str] = None,
    department: Optional[str] = None,
    skills: Optional[str] = None,
) -> Dict[str, Any]:
    ts = _now()
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO users (name, email, phone, department, skills, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (name, email, phone, department, skills, ts, ts),
        ).fetchone()
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
            f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?", tuple(params)
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)
