"""Logging setup.

Human-readable by default; set LOG_FORMAT=json for one JSON object per line,
which is what a log collector wants in a container. Configured once, from
`setup_logging()`, so importing a module never mutates global logging state.
"""

import json
import logging
import sys
from typing import Optional

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("session", "request_id", "duration_ms", "path", "status"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """Configure the root logger once. Safe to call repeatedly."""
    global _configured
    if _configured:
        return

    from .config import LOG_FORMAT, LOG_LEVEL

    level = (level or LOG_LEVEL).upper()
    fmt = (fmt or LOG_FORMAT).lower()

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s %(name)-28s %(message)s",
                              datefmt="%H:%M:%S")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # The SDK's HTTP client is chatty at DEBUG and leaks request bodies.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
