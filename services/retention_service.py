"""Schedule retention drip messages when a user leaves the monitored chat."""

from __future__ import annotations

import logging
import time

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RetentionService:
    """Uses Redis ZSET (score=unix ts) for due retention sends."""

    def __init__(self, redis: Redis, *, zset_key: str = "retention:due") -> None:
        self._r = redis
        self._zset_key = zset_key

    async def schedule_first_step(self, user_id: int, first_delay_seconds: int) -> None:
        """Schedule step index 0."""
        fire_at = time.time() + max(first_delay_seconds, 1)
        member = f"{user_id}:0"
        await self._r.zadd(self._zset_key, {member: fire_at})

    async def schedule_next_step(self, user_id: int, step_index: int, delay_seconds: int) -> None:
        fire_at = time.time() + max(delay_seconds, 1)
        member = f"{user_id}:{step_index}"
        await self._r.zadd(self._zset_key, {member: fire_at})

    async def pop_due(self, now: float | None = None, count: int = 50) -> list[str]:
        """Pop due members with score <= now (non-atomic multi; acceptable at drip cadence)."""
        ts = now or time.time()
        members = await self._r.zrangebyscore(self._zset_key, 0, ts, start=0, num=count)
        out: list[str] = []
        for m in members:
            await self._r.zrem(self._zset_key, m)
            out.append(m if isinstance(m, str) else m.decode("utf-8"))
        return out
