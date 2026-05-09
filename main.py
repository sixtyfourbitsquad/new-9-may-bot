"""
ASGI entrypoint: FastAPI webhook + background asyncio workers.

Run: ``uvicorn main:app --host 0.0.0.0 --port 8000`` from project root.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvloop
from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update
from bot.application import build_application, create_redis_client, seed_initial_owner
from configs.settings import Settings, get_settings
from database.pool import close_pool, get_pool, init_pool
from database.repositories.admins import AdminRepository
from workers.broadcast_worker import broadcast_worker_loop
from workers.retention_worker import retention_worker_loop
from workers.scheduler_worker import scheduler_worker_loop

logger = logging.getLogger(__name__)


def _setup_uvloop() -> None:
    """Install uvloop policy when available (Linux/macOS)."""
    try:
        uvloop.install()
    except Exception:
        logger.warning("uvloop not installed or unsupported; using default loop")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    os.makedirs(settings.storage_dir, exist_ok=True)

    await init_pool(settings)
    pool = get_pool()
    redis = create_redis_client(settings)

    admins_repo = AdminRepository(pool)
    await seed_initial_owner(settings, admins_repo)

    application = build_application(settings=settings, redis=redis, pool=pool)
    await application.initialize()
    await application.start()

    stop_event = asyncio.Event()
    bc_svc = application.bot_data["services"]["broadcast"]

    tasks = [
        asyncio.create_task(
            broadcast_worker_loop(
                bot=application.bot,
                redis=redis,
                settings=settings,
                broadcasts=application.bot_data["repos"]["broadcasts"],
                users=application.bot_data["repos"]["users"],
                bc_service=bc_svc,
                stop_event=stop_event,
            ),
            name="broadcast-worker",
        ),
        asyncio.create_task(
            scheduler_worker_loop(
                bot=application.bot,
                settings=settings,
                scheduled_repo=application.bot_data["repos"]["scheduled"],
                broadcasts_repo=application.bot_data["repos"]["broadcasts"],
                broadcast_service=bc_svc,
                stop_event=stop_event,
            ),
            name="scheduler-worker",
        ),
        asyncio.create_task(
            retention_worker_loop(
                bot=application.bot,
                settings=settings,
                settings_repo=application.bot_data["repos"]["settings"],
                retention=application.bot_data["services"]["retention"],
                stop_event=stop_event,
            ),
            name="retention-worker",
        ),
    ]

    app.state.settings = settings
    app.state.ptb = application
    app.state.redis = redis
    app.state.stop_event = stop_event
    app.state.worker_tasks = tasks

    secret = settings.webhook_secret.get_secret_value()
    webhook_route = settings.webhook_path.format(secret=secret)
    await application.bot.set_webhook(
        url=settings.webhook_full_url(),
        allowed_updates=None,
        secret_token=settings.telegram_webhook_secret_token.get_secret_value()
        if settings.telegram_webhook_secret_token
        else None,
        drop_pending_updates=True,
    )
    logger.info("Webhook set to %s route_suffix=%s", settings.webhook_full_url(), webhook_route)

    yield

    stop_event.set()
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t

    await application.stop()
    await application.shutdown()
    await redis.aclose()
    await close_pool()


def create_app() -> FastAPI:
    from utils.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="Telegram Community Bot", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    route_path = settings.webhook_path.format(secret=settings.webhook_secret.get_secret_value())

    @app.post(route_path)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        s: Settings = request.app.state.settings
        if s.telegram_webhook_secret_token is not None:
            expected = s.telegram_webhook_secret_token.get_secret_value()
            if x_telegram_bot_api_secret_token != expected:
                raise HTTPException(status_code=403, detail="Invalid webhook secret header")

        data = await request.json()
        application = request.app.state.ptb
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"ok": True}

    return app


app = create_app()


def main() -> None:
    _setup_uvloop()
    settings = get_settings()
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        factory=False,
    )


if __name__ == "__main__":
    main()
