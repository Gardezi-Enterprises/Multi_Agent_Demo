"""Transport-agnostic helpers shared by the API and the tests.

These were inline in the old stdlib server; extracted here so the FastAPI app
and the unit tests import one copy. Nothing in this module knows about FastAPI.
"""

import json
import threading
import time
from pathlib import Path
from typing import Callable, List

from multi_agent_system.commands import expand as expand_command
from multi_agent_system.config import UPLOAD_DIR
from multi_agent_system.documents import extract_text
from multi_agent_system.logging_config import get_logger

log = get_logger("serving")


def size_label(path: Path) -> str:
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def event_to_json(event) -> dict:
    """Flatten a TraceEvent for the browser.

    A streamed event is emitted the moment the call starts, before its result
    exists; "running" is accurate then, and the final trace replaces it.
    """
    args = ", ".join(
        f"{k}={json.dumps(v, default=str)[:38]}"
        for k, v in event.args.items()
        if v not in (None, "", [])
    )
    if isinstance(event.result, dict):
        status = event.result.get("status", "unknown")
    elif event.result is None:
        status = "running"
    else:
        status = "unknown"
    return {
        "agent": event.agent, "kind": event.kind, "name": event.name,
        "args": args, "status": status,
    }


def downloads_from_trace(trace: list) -> list:
    """Collect documents the tools produced, for download buttons.

    `name` is the on-disk file used to fetch it; `download_name` is the friendly
    name the browser saves it as (defaults to the disk name when a tool doesn't
    specify one).
    """
    found, seen = [], set()
    for event in trace:
        result = event.result if isinstance(event.result, dict) else {}
        path_str = result.get("file_path")
        if not path_str:
            continue
        path = Path(path_str)
        if not path.exists() or path.name in seen:
            continue
        seen.add(path.name)
        found.append({
            "name": path.name,
            "download_name": result.get("download_name") or path.name,
            "size": size_label(path),
        })
    return found


def build_message(message: str, files: list) -> str:
    """Expand a slash command and inline the text of any attached files."""
    expanded = expand_command(message)
    if not files:
        return expanded

    blocks = []
    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            path = UPLOAD_DIR / path.name
        # Never read outside the upload directory, whatever the client sends.
        try:
            path.resolve().relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            blocks.append(f"[Rejected attachment outside the upload directory: {path.name}]")
            continue
        if not path.exists():
            blocks.append(f"[Attachment missing: {path.name}]")
            continue
        text, error = extract_text(str(path))
        header = f'Attached file "{path.name}" (saved at: {path})'
        blocks.append(
            f"{header}\n[This file's text could not be read: {error}]" if error
            else f'{header}\nContents:\n"""\n{text}\n"""'
        )

    return (
        f"{expanded}\n\n--- Attached files ---\n" + "\n\n".join(blocks) + "\n"
        "--- end of attached files ---\n"
        "Use the file contents above where relevant. When a tool needs a file "
        "path (for example an email attachment), use the exact 'saved at' path."
    )


class ObservableTrace(list):
    """A trace list that notifies a callback as events are appended.

    Lets the server stream each delegation and tool call to the browser while
    the turn is still running, without the agent runtime knowing about HTTP.
    """

    def __init__(self, on_append: Callable):
        super().__init__()
        self._on_append = on_append

    def append(self, item) -> None:
        super().append(item)
        try:
            self._on_append(item)
        except Exception:  # a broken client must not break the agent turn
            log.debug("trace listener failed", exc_info=True)


class RateLimiter:
    """Fixed-window per-key limiter."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits = {}
        self._guard = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._guard:
            start, count = self._hits.get(key, (now, 0))
            if now - start > self.window:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            if len(self._hits) > 10_000:  # bound memory
                self._hits = {k: v for k, v in self._hits.items() if now - v[0] < self.window}
            return count <= self.limit


__all__ = [
    "size_label", "event_to_json", "downloads_from_trace", "build_message",
    "ObservableTrace", "RateLimiter",
]
