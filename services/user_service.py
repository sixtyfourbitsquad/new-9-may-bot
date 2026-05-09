"""High-level user collection and stats."""

from __future__ import annotations

from typing import Any, Optional

import asyncpg
from telegram import Update, User

from database.repositories.users import UserRepository


class UserService:
    """Coordinates user upserts from Telegram updates."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._repo = UserRepository(pool)

    async def ingest_from_update(
        self,
        update: Update,
        *,
        source_channel: Optional[str] = None,
        increment_messages: bool = False,
    ) -> None:
        """Extract user from update and upsert."""
        user = update.effective_user
        if user is None:
            return
        await self._repo.upsert_from_telegram(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            language_code=user.language_code,
            source_channel=source_channel,
            increment_messages=increment_messages,
        )

    async def ingest_user(self, user: User, *, source_channel: Optional[str] = None) -> None:
        await self._repo.upsert_from_telegram(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            language_code=user.language_code,
            source_channel=source_channel,
            increment_messages=False,
        )

    async def stats(self) -> dict[str, Any]:
        return await self._repo.get_stats_snapshot()
