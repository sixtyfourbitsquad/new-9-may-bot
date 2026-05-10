"""Human-readable labels for retention delays (stored as seconds in DB)."""

from typing import Any, Mapping


def retention_delay_seconds(row: Mapping[str, Any], *, default: int) -> int:
    """Resolve delay_seconds; ``0`` is valid (instant). Do not use ``or`` — ``0`` is falsy."""
    v = row.get("delay_seconds")
    if v is None:
        return default
    return int(v)


def format_retention_delay_human(seconds: int) -> str:
    if seconds <= 0:
        return "instant"
    hours = seconds / 3600.0
    if abs(hours - round(hours)) < 1e-9:
        n = int(round(hours))
        return "1 hour" if n == 1 else f"{n} hours"
    text = f"{hours:.3g}".rstrip("0").rstrip(".")
    return f"{text} hours"
