"""Enqueue broadcast batches to Redis and expose pause/resume/cancel keys."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import orjson
from redis.asyncio import Redis

from database.repositories.broadcasts import BroadcastRepository
from database.repositories.users import UserRepository
from models.domain import BroadcastStatus

logger = logging.getLogger(__name__)


def _dumps(obj: Any) -> str:
    return orjson.dumps(obj).decode("utf-8")


class BroadcastService:
    """Orchestrates DB row + Redis queue for ultra-fast fan-out."""

    def __init__(
        self,
        *,
        redis: Redis,
        broadcasts: BroadcastRepository,
        users: UserRepository,
        queue_key: str,
        chunk_size: int,
    ) -> None:
        self._r = redis
        self._broadcasts = broadcasts
        self._users = users
        self._queue_key = queue_key
        self._chunk_size = chunk_size

    def _pause_key(self, bid: int) -> str:
        return f"broadcast:pause:{bid}"

    def _stats_key(self, bid: int) -> str:
        return f"broadcast:stats:{bid}"

    async def enqueue_broadcast(self, broadcast_id: int) -> int:
        """Split recipients into chunk jobs on Redis list. Returns job count."""
        lock_key = f"broadcast:enqueue_lock:{broadcast_id}"
        got = await self._r.set(lock_key, "1", nx=True, ex=600)
        if not got:
            logger.warning("Enqueue lock held for broadcast %s", broadcast_id)
            return 0

        batches = await self._users.iter_recipient_batches(self._chunk_size)
        total_targets = sum(len(b) for b in batches)
        await self._broadcasts.update_counters(broadcast_id, total_targets=total_targets)
        await self._broadcasts.update_status(broadcast_id, BroadcastStatus.QUEUED)
        await self._broadcasts.mark_started(broadcast_id)

        await self._r.hset(
            self._stats_key(broadcast_id),
            mapping={
                "total": str(total_targets),
                "processed": "0",
                "delivered": "0",
                "failed": "0",
                "blocked": "0",
                "remaining": str(total_targets),
                "started_ts": str(datetime.now(timezone.utc).timestamp()),
            },
        )
        await self._r.delete(self._pause_key(broadcast_id))

        count = 0
        for chunk in batches:
            job = {"broadcast_id": broadcast_id, "user_ids": chunk}
            await self._r.rpush(self._queue_key, _dumps(job))
            count += 1
        logger.info("Enqueued %s jobs for broadcast %s", count, broadcast_id)
        return count

    async def set_paused(self, broadcast_id: int, paused: bool) -> None:
        if paused:
            await self._r.set(self._pause_key(broadcast_id), "1", ex=86400 * 7)
            await self._broadcasts.update_status(broadcast_id, BroadcastStatus.PAUSED)
        else:
            await self._r.delete(self._pause_key(broadcast_id))
            await self._broadcasts.update_status(broadcast_id, BroadcastStatus.RUNNING)

    async def cancel(self, broadcast_id: int) -> None:
        await self._r.set(f"broadcast:cancel:{broadcast_id}", "1", ex=86400)
        await self.set_paused(broadcast_id, True)
        await self._broadcasts.update_status(broadcast_id, BroadcastStatus.CANCELLED)
        await self._broadcasts.mark_finished(broadcast_id, BroadcastStatus.CANCELLED)

    async def is_paused(self, broadcast_id: int) -> bool:
        return bool(await self._r.exists(self._pause_key(broadcast_id)))

    async def is_cancelled(self, broadcast_id: int) -> bool:
        return bool(await self._r.exists(f"broadcast:cancel:{broadcast_id}"))

    async def stats_snapshot(self, broadcast_id: int) -> dict[str, Any]:
        h = await self._r.hgetall(self._stats_key(broadcast_id))
        return dict(h)
