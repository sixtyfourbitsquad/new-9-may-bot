"""Welcome sequence: substitute {name} and send stored payloads."""

from __future__ import annotations

import copy
import logging
from typing import Any

from telegram import Bot

from database.repositories.settings_repo import SettingsRepository
from services.outbound_sender import send_from_payload
from utils.payload_coerce import coerce_payload_dict

logger = logging.getLogger(__name__)


def substitute_name_in_payload(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Deep-copy payload and replace ``{name}`` in text/caption fields."""
    p: dict[str, Any] = copy.deepcopy(payload)
    disp = name or ""
    if isinstance(p.get("text"), str):
        p["text"] = p["text"].replace("{name}", disp)
    if isinstance(p.get("caption"), str):
        p["caption"] = p["caption"].replace("{name}", disp)
    items = p.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("caption"), str):
                it["caption"] = it["caption"].replace("{name}", disp)
    return p


async def send_welcome_sequence(
    bot: Bot,
    *,
    chat_id: int,
    display_name: str,
    settings_repo: SettingsRepository,
) -> None:
    steps = await settings_repo.list_welcome_steps()
    for row in sorted(steps, key=lambda r: int(r.get("step_order") or 0)):
        raw = coerce_payload_dict(row.get("payload"))
        payload = substitute_name_in_payload(raw, display_name)
        try:
            await send_from_payload(bot, chat_id=chat_id, payload=payload)
        except Exception:
            logger.exception("Welcome step failed step_order=%s", row.get("step_order"))
