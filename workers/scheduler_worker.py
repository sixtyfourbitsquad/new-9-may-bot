"""Poll PostgreSQL for due scheduled_jobs and execute them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot

from configs.settings import Settings
from database.repositories.broadcasts import BroadcastRepository
from database.repositories.scheduled import ScheduledRepository
from database.repositories.users import UserRepository
from models.domain import BroadcastStatus
from services.broadcast_service import BroadcastService
from services.outbound_sender import send_from_payload
from services.welcome_flow import substitute_name_in_payload
from utils.payload_coerce import coerce_payload_dict

logger = logging.getLogger(__name__)


async def scheduler_worker_loop(
    *,
    bot: Bot,
    settings: Settings,
    scheduled_repo: ScheduledRepository,
    broadcasts_repo: BroadcastRepository,
    broadcast_service: BroadcastService,
    users: UserRepository,
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
                    msg = coerce_payload_dict(dict(payload["message"]))
                    fn = await users.get_first_name(uid)
                    msg = substitute_name_in_payload(msg, fn or "")
                    await send_from_payload(bot, chat_id=uid, payload=msg)
                elif mode == "broadcast_enqueue":
                    bid = await broadcasts_repo.create_broadcast(
                        created_by=int(payload.get("created_by") or 0),
                        payload=dict(payload["message"]),
                        status=BroadcastStatus.QUEUED,
                    )
                    await broadcast_service.enqueue_broadcast(bid)
                    intr = payload.get("interval_hours")
                    if intr is not None:
                        try:
                            hours = float(intr)
                            if hours > 0:
                                next_run = datetime.now(timezone.utc) + timedelta(hours=hours)
                                chain_payload = dict(payload)
                                await scheduled_repo.create_job(
                                    created_by=int(payload.get("created_by") or 0),
                                    run_at=next_run,
                                    payload=chain_payload,
                                )
                                logger.info(
                                    "Scheduled repeating broadcast chain job after #%s in %s h",
                                    jid,
                                    hours,
                                )
                        except (TypeError, ValueError):
                            logger.warning("Bad interval_hours on job %s: %s", jid, intr)
                await scheduled_repo.mark_sent(jid)
            except Exception:
                logger.exception("Scheduled job %s failed", jid)
                await scheduled_repo.mark_failed(jid)
        await asyncio.sleep(settings.scheduler_tick_seconds)
