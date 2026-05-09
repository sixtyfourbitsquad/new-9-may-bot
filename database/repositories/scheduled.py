"""Scheduled one-time jobs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import asyncpg


class ScheduledRepository:
    """scheduled_jobs table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_job(self, *, created_by: int, run_at: datetime, payload: dict[str, Any]) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO scheduled_jobs (created_by, run_at, payload, status)
                VALUES ($1, $2, $3::jsonb, 'pending')
                RETURNING id;
                """,
                created_by,
                run_at,
                json.dumps(payload),
            )
        return int(row["id"])

    async def due_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM scheduled_jobs
                WHERE status = 'pending' AND run_at <= now()
                ORDER BY run_at
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def mark_sent(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'sent', processed_at = now()
                WHERE id = $1;
                """,
                job_id,
            )

    async def mark_failed(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'failed', processed_at = now()
                WHERE id = $1;
                """,
                job_id,
            )

    async def cancel(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'cancelled', processed_at = now()
                WHERE id = $1 AND status = 'pending';
                """,
                job_id,
            )

    async def list_upcoming(self, limit: int = 25) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, created_by, run_at, payload, status, created_at
                FROM scheduled_jobs
                WHERE status = 'pending'
                ORDER BY run_at ASC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]
