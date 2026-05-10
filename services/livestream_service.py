"""Cooldown + cached invite links for livestream notifications."""

from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import Redis
from telegram import Bot
from telegram.error import TelegramError

from utils.flood import with_flood_wait

logger = logging.getLogger(__name__)


class LivestreamService:
    """Redis-backed cooldown and optional invite link cache."""

    def __init__(self, redis: Redis, prefix: str) -> None:
        self._r = redis
        self._prefix = prefix

    def _cooldown_key(self, chat_id: int) -> str:
        return f"{self._prefix}cool:{chat_id}"

    async def should_notify(self, chat_id: int, cooldown_seconds: int) -> bool:
        """Return True if not within cooldown window."""
        key = self._cooldown_key(chat_id)
        ok = await self._r.set(key, "1", nx=True, ex=max(cooldown_seconds, 1))
        return bool(ok)

    async def resolve_invite_link(self, bot: Bot, chat_id: int) -> Optional[str]:
        """Resolve a join link: primary invite, chat.invite_link, or newly created link."""
        try:
            link = await with_flood_wait(lambda: bot.export_chat_invite_link(chat_id))
            if link:
                return str(link)
        except TelegramError as e:
            logger.warning("export_chat_invite_link failed for %s: %s", chat_id, e)

        try:
            chat = await with_flood_wait(lambda: bot.get_chat(chat_id))
            inv = getattr(chat, "invite_link", None)
            if inv:
                return str(inv)
        except TelegramError as e:
            logger.warning("get_chat (invite_link) failed for %s: %s", chat_id, e)

        try:
            created = await with_flood_wait(lambda: bot.create_chat_invite_link(chat_id))
            il = getattr(created, "invite_link", None)
            if il:
                return str(il)
        except TelegramError as e:
            logger.warning("create_chat_invite_link failed for %s: %s", chat_id, e)

        return None
