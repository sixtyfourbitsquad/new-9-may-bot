"""Normalize Telegram / HTTPS URLs from admin input."""

from __future__ import annotations


def normalize_manual_live_url(raw: object) -> str | None:
    """
    Accept https URLs or bare t.me/telegram.me links for inline keyboard url buttons.
    Returns None if empty or not usable.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("https://") or low.startswith("http://"):
        return s
    if low.startswith("t.me/") or low.startswith("telegram.me/"):
        return "https://" + s.split("://", 1)[-1].lstrip("/")
    return None
