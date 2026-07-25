"""Per-user chat history, stored server-side.

Every conversation is owned by an operator account (by username), and every
query in this module is scoped to that owner. There is no code path that
returns a conversation to anyone but its owner, so cross-user history sharing
is impossible by construction — a user requesting someone else's conversation
id simply gets nothing back.
"""

import json
import secrets
import threading
from datetime import datetime, timezone
from typing import List, Optional

from .db.database import get_connection, init_db, row_to_dict

_TITLE_LIMIT = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return "c_" + secrets.token_urlsafe(12)


def create(owner: str, title: str = "New chat") -> dict:
    init_db()
    cid = _new_id()
    ts = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversations (id, owner, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, owner.strip(), (title or "New chat")[:_TITLE_LIMIT], ts, ts),
        )
    return {"id": cid, "title": (title or "New chat")[:_TITLE_LIMIT], "updated_at": ts}


def list_for(owner: str) -> List[dict]:
    """Every conversation belonging to this owner, most recent first."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM conversations "
            "WHERE owner = ? COLLATE NOCASE ORDER BY updated_at DESC, id DESC",
            (owner.strip(),),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def owns(owner: str, conversation_id: str) -> bool:
    """True only if this owner owns the given conversation."""
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND owner = ? COLLATE NOCASE",
            (conversation_id, owner.strip()),
        ).fetchone()
    return row is not None


def get_messages(owner: str, conversation_id: str) -> Optional[List[dict]]:
    """Return the conversation's messages, or None if it isn't the owner's."""
    if not owns(owner, conversation_id):
        return None
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content, trace, downloads FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "role": r["role"],
            "content": r["content"],
            "trace": json.loads(r["trace"]) if r["trace"] else [],
            "downloads": json.loads(r["downloads"]) if r["downloads"] else [],
        })
    return out


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    trace: Optional[list] = None,
    downloads: Optional[list] = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, trace, downloads, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                conversation_id, role, content,
                json.dumps(trace) if trace else None,
                json.dumps(downloads) if downloads else None,
                _now(),
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id)
        )


def set_title(owner: str, conversation_id: str, title: str) -> bool:
    if not owns(owner, conversation_id):
        return False
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            ((title or "New chat").strip()[:_TITLE_LIMIT] or "New chat", _now(), conversation_id),
        )
    return True


def title_if_default(owner: str, conversation_id: str, title: str) -> None:
    """Set the title only if it is still the default, so the first message names it."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ? AND owner = ? COLLATE NOCASE",
            (conversation_id, owner.strip()),
        ).fetchone()
        if row and row["title"] == "New chat":
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                ((title or "New chat").strip()[:_TITLE_LIMIT] or "New chat", conversation_id),
            )


def delete(owner: str, conversation_id: str) -> bool:
    if not owns(owner, conversation_id):
        return False
    with get_connection() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return True


def clear_all(owner: str) -> int:
    """Delete every conversation for an owner. Returns how many were removed."""
    convs = list_for(owner)
    for c in convs:
        delete(owner, c["id"])
    return len(convs)


# Per-conversation locks so two concurrent turns on the same chat can't interleave.
_locks: dict = {}
_locks_guard = threading.Lock()


def lock_for(conversation_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(conversation_id)
        if lock is None:
            lock = threading.Lock()
            _locks[conversation_id] = lock
            if len(_locks) > 5000:  # bound memory
                for key in list(_locks)[:1000]:
                    if key != conversation_id:
                        _locks.pop(key, None)
        return lock


def build_history(messages: List[dict]):
    """Reconstruct the agent's turn history from stored messages.

    Prior turns are replayed as plain text (user/model) Contents — enough for the
    model to keep context. Tool-call internals are not persisted; they only
    matter within a single turn's loop, which the runtime handles live.
    """
    from google.genai import types

    history = []
    for m in messages:
        if not m.get("content"):
            continue
        role = "user" if m["role"] == "user" else "model"
        history.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
    return history
