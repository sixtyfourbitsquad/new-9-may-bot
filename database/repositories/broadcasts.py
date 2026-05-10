"""Broadcast and broadcast_logs repositories."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from models.domain import BroadcastStatus
from utils.payload_coerce import coerce_payload_dict


class BroadcastRepository:
    """Persistence for broadcasts and per-user logs."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_broadcast(
        self,
        *,
        created_by: int,
        payload: dict[str, Any],
        scheduled_at: Optional[datetime] = None,
        status: BroadcastStatus = BroadcastStatus.DRAFT,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO broadcasts (created_by, payload, scheduled_at, status)
                VALUES ($1, $2::jsonb, $3, $4)
                RETURNING id;
                """,
                created_by,
                json.dumps(payload),
                scheduled_at,
                status.value,
            )
        return int(row["id"])

    async def update_status(self, broadcast_id: int, status: BroadcastStatus) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE broadcasts SET status = $2, updated_at = now()
                WHERE id = $1;
                """,
                broadcast_id,
                status.value,
            )

    async def update_counters(
        self,
        broadcast_id: int,
        *,
        total_targets: Optional[int] = None,
        delivered: Optional[int] = None,
        failed: Optional[int] = None,
        blocked: Optional[int] = None,
    ) -> None:
        sets: list[str] = ["updated_at = now()"]
        args: list[Any] = [broadcast_id]
        idx = 2
        if total_targets is not None:
            sets.append(f"total_targets = ${idx}")
            args.append(total_targets)
            idx += 1
        if delivered is not None:
            sets.append(f"delivered_count = ${idx}")
            args.append(delivered)
            idx += 1
        if failed is not None:
            sets.append(f"failed_count = ${idx}")
            args.append(failed)
            idx += 1
        if blocked is not None:
            sets.append(f"blocked_count = ${idx}")
            args.append(blocked)
            idx += 1
        sql = f"UPDATE broadcasts SET {', '.join(sets)} WHERE id = $1;"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def mark_started(self, broadcast_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE broadcasts
                SET started_at = COALESCE(started_at, now()),
                    updated_at = now()
                WHERE id = $1;
                """,
                broadcast_id,
            )

    async def mark_finished(self, broadcast_id: int, status: BroadcastStatus) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE broadcasts
                SET finished_at = now(),
                    status = $2,
                    updated_at = now()
                WHERE id = $1;
                """,
                broadcast_id,
                status.value,
            )

    async def get_payload(self, broadcast_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT payload FROM broadcasts WHERE id = $1;", broadcast_id)
        if row is None:
            return {}
        return coerce_payload_dict(row["payload"])

    async def get_row(self, broadcast_id: int) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM broadcasts WHERE id = $1;", broadcast_id)
        return dict(row) if row else None

    async def log_recipient(
        self,
        *,
        broadcast_id: int,
        user_id: int,
        status: str,
        error_code: Optional[str] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO broadcast_logs (broadcast_id, user_id, status, error_code)
                VALUES ($1, $2, $3, $4);
                """,
                broadcast_id,
                user_id,
                status,
                error_code,
            )

    async def list_recent(self, limit: int = 15) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, status, created_at, total_targets, delivered_count, failed_count, blocked_count
                FROM broadcasts
                ORDER BY id DESC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def list_active(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, status, created_at, total_targets, delivered_count, failed_count, blocked_count
                FROM broadcasts
                WHERE status IN ('queued', 'running', 'paused')
                ORDER BY id DESC;
                """
            )
        return [dict(r) for r in rows]
