"""Structured logging and an append-only audit trail.

Two concerns live here (plan decision #5):

* ``configure_logging`` sets up JSON-structured application logs with a filter
  that redacts anything that looks like a secret.
* ``AuditLog`` appends security-relevant events (tool calls, config changes,
  token rotation, failed logins) to a JSON-lines file under ``/data``. We log the
  tool name, service, and outcome - never raw tool arguments, API keys, or tokens.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Patterns whose values must never be written to logs.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|apikey|token|password|secret|authorization)", re.IGNORECASE
)
_REDACTED = "***redacted***"


def redact(value: Any) -> Any:
    """Recursively redact secret-looking values in a structure for safe logging."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _SECRET_KEY_PATTERN.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class _RedactingFilter(logging.Filter):
    """Redacts secret-looking keys found in ``record.extra`` style dict args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        return True


class _JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter; avoids a third-party dependency."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Promote any structured "extra" fields attached to the record.
        for key, val in getattr(record, "__dict__", {}).items():
            if key.startswith("ctx_"):
                payload[key[4:]] = redact(val)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging to emit redacted JSON lines to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


class AuditLog:
    """Append-only JSON-lines audit log stored under the data directory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: Any) -> None:
        """Append one audit event. Fields are redacted before writing."""
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **redact(fields),
        }
        line = json.dumps(entry, default=str)
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
