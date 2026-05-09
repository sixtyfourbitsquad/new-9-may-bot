"""Redis SET NX EX locks for broadcast jobs / singleton workers."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis


class DistributedLock:
    """Best-effort distributed lock."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def acquire(self, name: str, ttl_seconds: int = 30) -> str | None:
        """Return lock token if acquired."""
        token = str(uuid.uuid4())
        ok = await self._r.set(name, token, nx=True, ex=ttl_seconds)
        return token if ok else None

    async def release(self, name: str, token: str) -> None:
        """Release lock only if token matches (Lua)."""
        lua = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
          return redis.call("DEL", KEYS[1])
        else
          return 0
        end
        """
        await self._r.eval(lua, 1, name, token)
