"""PG-backed onboarding drip jobs (+1h / +1d / +3d style offsets from anchor)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class OnboardingRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_messages(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM onboarding_messages ORDER BY step_order ASC;"
            )
        return [dict(r) for r in rows]

    async def upsert_message(self, step_order: int, delay_seconds: int, payload: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO onboarding_messages (step_order, delay_seconds, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (step_order) DO UPDATE SET
                    delay_seconds = EXCLUDED.delay_seconds,
                    payload = EXCLUDED.payload;
                """,
                step_order,
                delay_seconds,
                json.dumps(payload),
            )

    async def delete_message(self, step_order: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM onboarding_messages WHERE step_order = $1;",
                step_order,
            )

    async def enqueue_for_user(self, user_id: int, anchor: datetime) -> int:
        """Insert pending jobs for each configured step with non-empty payload. Returns count inserted."""
        rows = await self.list_messages()
        inserted = 0
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        async with self._pool.acquire() as conn:
            for r in rows:
                pl = dict(r.get("payload") or {})
                if not pl or pl == {}:
                    continue
                delay = int(r.get("delay_seconds") or 0)
                so = int(r["step_order"])
                fire_at = anchor.timestamp() + max(delay, 0)
                fire_dt = datetime.fromtimestamp(fire_at, tz=timezone.utc)
                await conn.execute(
                    """
                    INSERT INTO onboarding_drip_jobs (user_id, step_order, fire_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, step_order) DO UPDATE SET
                        fire_at = EXCLUDED.fire_at,
                        sent_at = NULL;
                    """,
                    user_id,
                    so,
                    fire_dt,
                )
                inserted += 1
        return inserted

    async def list_due_ready(self, limit: int = 40) -> list[dict[str, Any]]:
        """Due jobs with message payload (skips steps whose template payload is empty)."""
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT j.id, j.user_id, j.step_order, m.payload
                FROM onboarding_drip_jobs j
                INNER JOIN onboarding_messages m ON m.step_order = j.step_order
                WHERE j.sent_at IS NULL
                  AND j.fire_at <= $1
                  AND NOT (COALESCE(m.payload, '{}'::jsonb) = '{}'::jsonb)
                ORDER BY j.fire_at ASC
                LIMIT $2;
                """,
                now,
                limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["payload"] = dict(d["payload"]) if d.get("payload") else {}
            out.append(d)
        return out

    async def mark_job_sent(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE onboarding_drip_jobs SET sent_at = now() WHERE id = $1 AND sent_at IS NULL;",
                job_id,
            )
