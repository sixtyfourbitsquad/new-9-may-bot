"""User persistence and bulk queries for broadcasting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import asyncpg

from models.domain import UserBroadcastStatus


class UserRepository:
    """CRUD and analytics for `users` table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_from_telegram(
        self,
        *,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        language_code: Optional[str],
        source_channel: Optional[str] = None,
        increment_messages: bool = False,
    ) -> None:
        """Insert or update user from Telegram user object fields."""
        async with self._pool.acquire() as conn:
            if increment_messages:
                await conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, first_name, language_code,
                        last_seen, source_channel, total_messages
                    )
                    VALUES ($1, $2, $3, $4, now(), $5, 1)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = COALESCE(EXCLUDED.username, users.username),
                        first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                        language_code = COALESCE(EXCLUDED.language_code, users.language_code),
                        last_seen = now(),
                        source_channel = COALESCE(EXCLUDED.source_channel, users.source_channel),
                        total_messages = users.total_messages + 1;
                    """,
                    user_id,
                    username,
                    first_name,
                    language_code,
                    source_channel,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, first_name, language_code,
                        last_seen, source_channel
                    )
                    VALUES ($1, $2, $3, $4, now(), $5)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = COALESCE(EXCLUDED.username, users.username),
                        first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                        language_code = COALESCE(EXCLUDED.language_code, users.language_code),
                        last_seen = now(),
                        source_channel = COALESCE(EXCLUDED.source_channel, users.source_channel);
                    """,
                    user_id,
                    username,
                    first_name,
                    language_code,
                    source_channel,
                )

    async def touch_seen(self, user_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_seen = now() WHERE user_id = $1;",
                user_id,
            )

    async def count_active_recipients(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)::bigint AS c FROM users
                WHERE is_active = TRUE AND broadcast_status = 'active';
                """
            )
            return int(row["c"]) if row else 0

    async def iter_recipient_batches(self, batch_size: int) -> Sequence[list[int]]:
        """Materialize all recipient ids in batches (generator-friendly via caller loop)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id FROM users
                WHERE is_active = TRUE AND broadcast_status = 'active'
                ORDER BY user_id;
                """
            )
        ids = [int(r["user_id"]) for r in rows]
        return [ids[i : i + batch_size] for i in range(0, len(ids), batch_size)]

    async def get_stats_snapshot(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)::bigint AS total,
                    COUNT(*) FILTER (WHERE is_active)::bigint AS active,
                    COUNT(*) FILTER (WHERE broadcast_status = 'blocked')::bigint AS blocked
                FROM users;
                """
            )
        return dict(row) if row else {}

    async def log_activity(self, user_id: int, action: str, meta: Optional[dict[str, Any]] = None) -> None:
        import json

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_activity (user_id, action, meta)
                VALUES ($1, $2, $3::jsonb);
                """,
                user_id,
                action,
                json.dumps(meta or {}),
            )

    async def set_broadcast_status(self, user_id: int, status: UserBroadcastStatus) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET broadcast_status = $2 WHERE user_id = $1;",
                user_id,
                status.value,
            )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
