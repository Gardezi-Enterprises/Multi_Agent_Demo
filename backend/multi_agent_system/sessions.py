"""Per-conversation agent sessions.

A single module-level Master Agent would mean every browser tab shares one
conversation history and one lock — fine for a demo, wrong for anything real.
This store gives each conversation its own orchestrator instance, with its own
history and its own lock, so concurrent users never see each other's context and
one slow turn does not block everyone else.

Sessions are held in memory and evicted by idle TTL and by count, so a
long-running process cannot grow without bound. For a multi-process deployment
this is the piece to move behind Redis; the interface would not change.
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .agents.master_agent import MasterAgent
from .config import SESSION_IDLE_TTL, SESSION_MAX
from .logging_config import get_logger

log = get_logger(__name__)


@dataclass
class Session:
    """One conversation: its agent, its lock, and its access times."""

    id: str
    agent: MasterAgent
    created_at: float
    last_used: float
    turns: int = 0
    # Serialises turns within a conversation without blocking other sessions.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def touch(self) -> None:
        self.last_used = time.monotonic()


class SessionStore:
    def __init__(self, max_sessions: int = SESSION_MAX, idle_ttl: float = SESSION_IDLE_TTL):
        self._sessions: Dict[str, Session] = {}
        self._guard = threading.Lock()
        self.max_sessions = max_sessions
        self.idle_ttl = idle_ttl

    # -- lifecycle ------------------------------------------------------------

    def _evict_locked(self) -> None:
        """Drop idle sessions, then the oldest if still over capacity.

        Caller must hold self._guard.
        """
        now = time.monotonic()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_used > self.idle_ttl]
        for sid in expired:
            self._sessions.pop(sid, None)
        if expired:
            log.info("evicted %d idle session(s)", len(expired))

        while len(self._sessions) > self.max_sessions:
            oldest = min(self._sessions.values(), key=lambda s: s.last_used)
            self._sessions.pop(oldest.id, None)
            log.info("evicted oldest session %s (capacity)", oldest.id[:8])

    def get_or_create(self, session_id: Optional[str] = None) -> Session:
        """Return the named session, creating it if it is unknown or expired."""
        with self._guard:
            self._evict_locked()
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.touch()
                return session

            # An unknown id from the client is honoured rather than replaced, so a
            # page reload keeps its conversation; only the server mints new ones.
            new_id = session_id or secrets.token_urlsafe(16)
            now = time.monotonic()
            session = Session(id=new_id, agent=MasterAgent(), created_at=now, last_used=now)
            self._sessions[new_id] = session
            # Enforce capacity *after* inserting; sweeping first would leave room
            # for one more and let the store settle one over its limit.
            self._evict_locked()
            log.info("created session %s (%d active)", new_id[:8], len(self._sessions))
            return session

    def reset(self, session_id: str) -> bool:
        """Clear a conversation's memory, keeping the session itself."""
        with self._guard:
            session = self._sessions.get(session_id)
        if session is None:
            return False
        with session.lock:
            session.agent.reset()
            session.turns = 0
            session.touch()
        return True

    def drop(self, session_id: str) -> bool:
        with self._guard:
            return self._sessions.pop(session_id, None) is not None

    # -- introspection --------------------------------------------------------

    def stats(self) -> dict:
        with self._guard:
            sessions: List[Session] = list(self._sessions.values())
        return {
            "active_sessions": len(sessions),
            "total_turns": sum(s.turns for s in sessions),
            "max_sessions": self.max_sessions,
            "idle_ttl_seconds": self.idle_ttl,
        }


store = SessionStore()
