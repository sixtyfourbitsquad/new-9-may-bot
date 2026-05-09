"""Telegram FloodWait handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from telegram.error import RetryAfter, TelegramError

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def with_flood_wait(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 8,
) -> T:
    """Execute async Telegram call with RetryAfter backoff."""
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except RetryAfter as e:
            attempt += 1
            if attempt > max_retries:
                raise
            wait = float(e.retry_after) + 0.5
            logger.warning("FloodWait retry_after=%s (attempt %s)", e.retry_after, attempt)
            await asyncio.sleep(wait)
        except TelegramError:
            raise
