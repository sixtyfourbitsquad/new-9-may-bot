"""Normalize DB JSON payloads to plain dicts (asyncpg / JSON quirks)."""

from __future__ import annotations

import json
from typing import Any


def coerce_payload_dict(raw: Any) -> dict[str, Any]:
    """Return a dict suitable for outbound_sender / welcome steps."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}
