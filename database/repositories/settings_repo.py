"""Welcome, retention, channel, livestream, inline buttons, system logs."""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


class SettingsRepository:
    """Misc configuration tables."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_channel_settings(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM channel_settings WHERE id = 1;")
        return dict(row) if row else {}

    async def set_monitored_chat(self, chat_id: Optional[int]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE channel_settings
                SET monitored_chat_id = $1, updated_at = now()
                WHERE id = 1;
                """,
                chat_id,
            )

    async def increment_join_requests_total(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE channel_settings
                SET join_requests_total = join_requests_total + 1, updated_at = now()
                WHERE id = 1;
                """
            )

    async def set_auto_approve_join_requests(self, enabled: bool) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE channel_settings
                SET auto_approve_join_requests = $1, updated_at = now()
                WHERE id = 1;
                """,
                enabled,
            )

    async def get_livestream_settings(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM livestream_settings WHERE id = 1;")
        return dict(row) if row else {}

    async def update_livestream_template(self, template: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE livestream_settings
                SET notification_template = $1, updated_at = now()
                WHERE id = 1;
                """,
                template,
            )

    async def list_welcome_steps(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM welcome_messages ORDER BY step_order ASC;"
            )
        return [dict(r) for r in rows]

    async def upsert_welcome_step(self, step_order: int, payload: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO welcome_messages (step_order, payload)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (step_order) DO UPDATE SET payload = EXCLUDED.payload;
                """,
                step_order,
                json.dumps(payload),
            )

    async def list_retention_steps(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM retention_messages ORDER BY step_order ASC;"
            )
        return [dict(r) for r in rows]

    async def upsert_retention_step(
        self, step_order: int, delay_seconds: int, payload: dict[str, Any]
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO retention_messages (step_order, delay_seconds, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (step_order) DO UPDATE SET
                    delay_seconds = EXCLUDED.delay_seconds,
                    payload = EXCLUDED.payload;
                """,
                step_order,
                delay_seconds,
                json.dumps(payload),
            )

    async def list_inline_presets(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, buttons, created_at FROM inline_buttons ORDER BY id DESC;"
            )
        return [dict(r) for r in rows]

    async def save_inline_preset(self, name: str, buttons: list[list[dict[str, Any]]]) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO inline_buttons (name, buttons)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (name) DO UPDATE SET buttons = EXCLUDED.buttons
                RETURNING id;
                """,
                name,
                json.dumps(buttons),
            )
        return int(row["id"])

    async def audit_log(self, level: str, source: str, message: str, context: Optional[dict] = None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO system_logs (level, source, message, context)
                VALUES ($1, $2, $3, $4::jsonb);
                """,
                level,
                source,
                message,
                json.dumps(context or {}),
            )

    async def set_retention_enabled(self, enabled: bool) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE channel_settings
                SET retention_enabled = $1, updated_at = now()
                WHERE id = 1;
                """,
                enabled,
            )

    async def update_livestream(
        self,
        *,
        notification_template: Optional[str] = None,
        cooldown_seconds: Optional[int] = None,
        banner_payload: Any = None,
        button_payload: Any = None,
        manual_live_url: Optional[str] = None,
    ) -> None:
        sets: list[str] = ["updated_at = now()"]
        args: list[Any] = []
        idx = 1
        if notification_template is not None:
            sets.append(f"notification_template = ${idx}")
            args.append(notification_template)
            idx += 1
        if cooldown_seconds is not None:
            sets.append(f"cooldown_seconds = ${idx}")
            args.append(cooldown_seconds)
            idx += 1
        if banner_payload is not None:
            sets.append(f"banner_payload = ${idx}::jsonb")
            args.append(json.dumps(banner_payload))
            idx += 1
        if button_payload is not None:
            sets.append(f"button_payload = ${idx}::jsonb")
            args.append(json.dumps(button_payload))
            idx += 1
        if manual_live_url is not None:
            sets.append(f"manual_live_url = ${idx}")
            v = manual_live_url.strip() if manual_live_url else None
            args.append(v)
            idx += 1
        sql = f"UPDATE livestream_settings SET {', '.join(sets)} WHERE id = 1;"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def delete_welcome_step(self, step_order: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM welcome_messages WHERE step_order = $1;", step_order)

    async def delete_retention_step(self, step_order: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM retention_messages WHERE step_order = $1;", step_order)

    async def delete_inline_preset(self, preset_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM inline_buttons WHERE id = $1;", preset_id)

    async def fetch_recent_logs(self, limit: int = 12) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, level, source, message, created_at
                FROM system_logs
                ORDER BY id DESC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]
