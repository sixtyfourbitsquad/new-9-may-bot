"""Poll PostgreSQL for due scheduled_jobs and execute them."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from configs.settings import Settings
from database.repositories.broadcasts import BroadcastRepository
from database.repositories.scheduled import ScheduledRepository
from models.domain import BroadcastStatus
from services.broadcast_service import BroadcastService
from services.outbound_sender import send_from_payload

logger = logging.getLogger(__name__)


async def scheduler_worker_loop(
    *,
    bot: Bot,
    settings: Settings,
    scheduled_repo: ScheduledRepository,
    broadcasts_repo: BroadcastRepository,
    broadcast_service: BroadcastService,
    stop_event: asyncio.Event,
) -> None:
    """Periodically flush due scheduled jobs."""
    while not stop_event.is_set():
        jobs = await scheduled_repo.due_jobs(limit=100)
        for job in jobs:
            jid = int(job["id"])
            payload = dict(job["payload"])
            mode = str(payload.get("mode") or "broadcast_enqueue")
            try:
                if mode == "single_user":
                    uid = int(payload["user_id"])
                    msg = dict(payload["message"])
                    await send_from_payload(bot, chat_id=uid, payload=msg)
                elif mode == "broadcast_enqueue":
                    bid = await broadcasts_repo.create_broadcast(
                        created_by=int(payload.get("created_by") or 0),
                        payload=dict(payload["message"]),
                        status=BroadcastStatus.QUEUED,
                    )
                    await broadcast_service.enqueue_broadcast(bid)
                await scheduled_repo.mark_sent(jid)
            except Exception:
                logger.exception("Scheduled job %s failed", jid)
                await scheduled_repo.mark_failed(jid)
        await asyncio.sleep(settings.scheduler_tick_seconds)
