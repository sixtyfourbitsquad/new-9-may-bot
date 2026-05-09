"""Redis-backed sliding window rate limiting."""

from __future__ import annotations

import time

from redis.asyncio import Redis


class RateLimitService:
    """Simple per-key token bucket using INCR + EXPIRE."""

    def __init__(self, redis: Redis, prefix: str) -> None:
        self._r = redis
        self._prefix = prefix

    async def allow(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        """Return True if under limit; increments counter."""
        redis_key = f"{self._prefix}{key}"
        pipe = self._r.pipeline()
        pipe.incr(redis_key)
        pipe.ttl(redis_key)
        count, ttl = await pipe.execute()
        if ttl == -1:
            await self._r.expire(redis_key, window_seconds)
        return int(count) <= limit

    async def allow_sliding(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        """Sliding window using sorted set (more accurate for bursts)."""
        redis_key = f"{self._prefix}sw:{key}"
        now = time.time()
        window_start = now - window_seconds
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window_seconds + 1)
        _, _, card, _ = await pipe.execute()
        return int(card) <= limit
