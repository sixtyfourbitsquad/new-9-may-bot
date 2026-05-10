"""Welcome sequence: substitute {name} and send stored payloads."""

from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from redis.asyncio import Redis
from telegram import Bot
from telegram.error import Forbidden

from database.repositories.settings_repo import SettingsRepository
from services.outbound_sender import send_from_payload
from utils.payload_coerce import coerce_payload_dict

logger = logging.getLogger(__name__)

_DEFAULT_WELCOME_DEDUP_TTL = 600


def substitute_name_in_payload(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Deep-copy payload and replace ``{name}`` in user-visible strings we store."""
    p: dict[str, Any] = copy.deepcopy(payload)
    disp = name or ""

    def sub(s: str) -> str:
        return s.replace("{name}", disp)

    if isinstance(p.get("text"), str):
        p["text"] = sub(p["text"])
    if isinstance(p.get("caption"), str):
        p["caption"] = sub(p["caption"])
    if isinstance(p.get("question"), str):
        p["question"] = sub(p["question"])
    opts = p.get("options")
    if isinstance(opts, list):
        p["options"] = [
            sub(str(o)) if isinstance(o, str) else o for o in opts
        ]
    items = p.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("caption"), str):
                it["caption"] = sub(it["caption"])
    rows = p.get("inline_keyboard")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, list):
                continue
            for btn in row:
                if isinstance(btn, dict) and isinstance(btn.get("text"), str):
                    btn["text"] = sub(btn["text"])
    return p


async def send_welcome_sequence(
    bot: Bot,
    *,
    chat_id: int,
    display_name: str,
    settings_repo: SettingsRepository,
    redis: Optional[Redis] = None,
    dedup_ttl_seconds: int = _DEFAULT_WELCOME_DEDUP_TTL,
) -> None:
    """
    Send configured welcome steps in private chat (chat_id = user's private id).

    If ``redis`` is set, skip when a welcome was already sent recently (dedup key),
    and set the key after a successful full sequence — avoids double sends when both
    ``chat_join_request`` and ``chat_member`` fire for the same join.
    """
    steps = await settings_repo.list_welcome_steps()
    if not steps:
        logger.warning(
            "Welcome skipped: no welcome_messages rows — configure Admin → New user welcome"
        )
        return
    uid = chat_id
    if redis:
        dedup_key = f"welcome:dedup:{uid}"
        if await redis.get(dedup_key):
            logger.info("Welcome sequence skipped (dedup) user_id=%s", uid)
            return
    for row in sorted(steps, key=lambda r: int(r.get("step_order") or 0)):
        raw = coerce_payload_dict(row.get("payload"))
        payload = substitute_name_in_payload(raw, display_name)
        try:
            await send_from_payload(bot, chat_id=chat_id, payload=payload)
        except Forbidden as e:
            logger.warning(
                "Welcome DM forbidden user_id=%s — user must open the bot and tap Start first: %s",
                chat_id,
                e,
            )
            return
        except Exception:
            logger.exception("Welcome step failed step_order=%s", row.get("step_order"))
    if redis:
        await redis.set(f"welcome:dedup:{uid}", "1", ex=max(dedup_ttl_seconds, 60))
