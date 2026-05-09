"""Parse admin-entered datetimes (ISO-8601, UTC default)."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso_utc(text: str) -> datetime:
    """Parse strings like ``2026-05-10T15:00:00`` or with ``Z`` suffix."""
    s = text.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
