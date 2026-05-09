"""Shared configuration defaults for tg_sync."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_API_URL = "http://127.0.0.1:8765"
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8787
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000


def resolve_db_path(value: str | None = None) -> Path:
    """Resolve the monitor DB path without creating or touching it."""
    raw = value or os.environ.get("TG_MONITOR_DB") or os.environ.get("DB_PATH") or "monitor.db"
    return Path(raw).expanduser()


def clamp_limit(value: int | str | None, default: int = DEFAULT_LIMIT) -> int:
    if value is None:
        return default
    limit = int(value)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, MAX_LIMIT)
