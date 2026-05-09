"""asyncpg connection pool lifecycle."""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from configs.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """Create global pool (call once at startup)."""
    global _pool
    if _pool is not None:
        return _pool
    s = settings or get_settings()
    _pool = await asyncpg.create_pool(
        dsn=s.postgres_dsn,
        min_size=s.postgres_pool_min,
        max_size=s.postgres_pool_max,
        command_timeout=60,
    )
    logger.info("PostgreSQL pool ready (min=%s max=%s)", s.postgres_pool_min, s.postgres_pool_max)
    return _pool


def get_pool() -> asyncpg.Pool:
    """Return initialized pool."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized; call init_pool() first.")
    return _pool


async def close_pool() -> None:
    """Graceful shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")
