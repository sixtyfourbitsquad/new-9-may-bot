"""Send retention drip messages when due."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.error import Forbidden

from configs.settings import Settings
from database.repositories.settings_repo import SettingsRepository
from services.outbound_sender import send_from_payload
from services.retention_service import RetentionService
from utils.payload_coerce import coerce_payload_dict
from utils.retention_display import retention_delay_seconds

logger = logging.getLogger(__name__)


async def retention_worker_loop(
    *,
    bot: Bot,
    settings: Settings,
    settings_repo: SettingsRepository,
    retention: RetentionService,
    stop_event: asyncio.Event,
) -> None:
    """Drain retention ZSET and send configured payloads."""
    while not stop_event.is_set():
        steps = await settings_repo.list_retention_steps()
        steps_sorted = sorted(steps, key=lambda r: int(r.get("step_order") or 0))
        if not steps_sorted:
            await asyncio.sleep(settings.retention_tick_seconds)
            continue

        due = await retention.pop_due(count=50)
        for member in due:
            try:
                user_part, step_part = member.split(":", 1)
                uid = int(user_part)
                step_idx = int(step_part)
                row = steps_sorted[step_idx] if step_idx < len(steps_sorted) else None
                if row is None:
                    continue
                msg_payload = coerce_payload_dict(row.get("payload"))
                await send_from_payload(bot, chat_id=uid, payload=msg_payload)
                nxt = step_idx + 1
                if nxt < len(steps_sorted):
                    delay = retention_delay_seconds(steps_sorted[nxt], default=3600)
                    await retention.schedule_next_step(uid, nxt, delay)
            except Forbidden:
                logger.warning(
                    "Retention DM skipped user_id=%s (blocked bot or never started chat)",
                    uid,
                )
            except Exception:
                logger.exception("Retention send failed for %s", member)
        await asyncio.sleep(settings.retention_tick_seconds)
