"""Consumes Redis broadcast queue jobs with concurrency control."""

from __future__ import annotations

import asyncio
import logging
import time
import orjson
from redis.asyncio import Redis
from telegram import Bot
from telegram.error import Forbidden, TelegramError

from configs.settings import Settings
from database.repositories.broadcasts import BroadcastRepository
from database.repositories.users import UserRepository
from models.domain import BroadcastStatus, UserBroadcastStatus
from services.broadcast_service import BroadcastService
from services.outbound_sender import send_from_payload

logger = logging.getLogger(__name__)


async def broadcast_worker_loop(
    *,
    bot: Bot,
    redis: Redis,
    settings: Settings,
    broadcasts: BroadcastRepository,
    users: UserRepository,
    bc_service: BroadcastService,
    stop_event: asyncio.Event,
) -> None:
    """Main consumer loop (embed as asyncio task)."""
    sem = asyncio.Semaphore(settings.broadcast_concurrency)

    async def handle_job(raw: str | bytes) -> None:
        job = orjson.loads(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        bid = int(job["broadcast_id"])
        uids = [int(x) for x in job["user_ids"]]

        if await bc_service.is_cancelled(bid):
            return

        payload = await broadcasts.get_payload(bid)
        if not payload:
            logger.error("Missing payload for broadcast %s", bid)
            return

        async def send_one(uid: int) -> None:
            async with sem:
                while await bc_service.is_paused(bid) and not await bc_service.is_cancelled(bid):
                    await asyncio.sleep(0.5)
                if await bc_service.is_cancelled(bid):
                    return
                try:
                    await send_from_payload(bot, chat_id=uid, payload=payload)
                    await broadcasts.log_recipient(broadcast_id=bid, user_id=uid, status="delivered")
                    pipe = redis.pipeline()
                    pipe.hincrby(f"broadcast:stats:{bid}", "delivered", 1)
                    pipe.hincrby(f"broadcast:stats:{bid}", "remaining", -1)
                    pipe.hincrby(f"broadcast:stats:{bid}", "processed", 1)
                    await pipe.execute()
                except Forbidden:
                    await users.set_broadcast_status(uid, UserBroadcastStatus.BLOCKED)
                    await broadcasts.log_recipient(
                        broadcast_id=bid, user_id=uid, status="blocked", error_code="403"
                    )
                    pipe = redis.pipeline()
                    pipe.hincrby(f"broadcast:stats:{bid}", "blocked", 1)
                    pipe.hincrby(f"broadcast:stats:{bid}", "remaining", -1)
                    pipe.hincrby(f"broadcast:stats:{bid}", "processed", 1)
                    await pipe.execute()
                except TelegramError as e:
                    await broadcasts.log_recipient(
                        broadcast_id=bid,
                        user_id=uid,
                        status="failed",
                        error_code=e.__class__.__name__,
                    )
                    pipe = redis.pipeline()
                    pipe.hincrby(f"broadcast:stats:{bid}", "failed", 1)
                    pipe.hincrby(f"broadcast:stats:{bid}", "remaining", -1)
                    pipe.hincrby(f"broadcast:stats:{bid}", "processed", 1)
                    await pipe.execute()

        await asyncio.gather(*(send_one(u) for u in uids))

        st = await redis.hgetall(f"broadcast:stats:{bid}")
        delivered = int(st.get("delivered") or 0)
        started_ts = float(st.get("started_ts") or time.time())
        elapsed = max(time.time() - started_ts, 0.001)
        speed = delivered / elapsed
        await redis.hset(f"broadcast:stats:{bid}", "speed", f"{speed:.4f}")

        total = int(st.get("total") or 0)
        processed = int(st.get("processed") or 0)
        if total > 0 and processed >= total:
            await broadcasts.mark_finished(bid, BroadcastStatus.COMPLETED)

    while not stop_event.is_set():
        item = await redis.blpop(settings.redis_broadcast_queue, timeout=2)
        if item is None:
            continue
        _, raw = item
        try:
            raw_str = raw if isinstance(raw, str) else raw.decode("utf-8")
            await handle_job(raw_str)
        except Exception:
            logger.exception("Broadcast job failed")
