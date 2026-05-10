"""Build and configure the python-telegram-bot Application."""

from __future__ import annotations

import logging

from redis.asyncio import Redis
from telegram.ext import Application

from configs.settings import Settings
from database.repositories.admins import AdminRepository
from database.repositories.broadcasts import BroadcastRepository
from database.repositories.onboarding_repo import OnboardingRepository
from database.repositories.scheduled import ScheduledRepository
from database.repositories.settings_repo import SettingsRepository
from database.repositories.users import UserRepository
from handlers.registration import register_handlers
from services.admin_fsm import AdminFsm
from services.broadcast_service import BroadcastService
from services.live_chat_service import LiveChatService
from services.livestream_service import LivestreamService
from services.rate_limit_service import RateLimitService
from services.redis_app import create_redis
from services.retention_service import RetentionService
from services.user_service import UserService

logger = logging.getLogger(__name__)


async def seed_initial_owner(settings: Settings, admins: AdminRepository) -> None:
    """Grant owner role when database has no owners."""
    if settings.initial_owner_id is None:
        return
    owners = await admins.count_owners()
    if owners > 0:
        return
    from models.domain import AdminRole

    await admins.add_admin(settings.initial_owner_id, AdminRole.OWNER, None)
    logger.warning("Seeded initial owner admin id=%s", settings.initial_owner_id)


def build_application(*, settings: Settings, redis: Redis, pool) -> Application:
    """Wire repositories/services into bot_data and register handlers."""
    admins = AdminRepository(pool)
    users_repo = UserRepository(pool)
    broadcasts_repo = BroadcastRepository(pool)
    scheduled_repo = ScheduledRepository(pool)
    settings_repo = SettingsRepository(pool)
    onboarding_repo = OnboardingRepository(pool)

    rate_lc = RateLimitService(redis, prefix=f"{settings.redis_rate_prefix}lc:")
    live_chat = LiveChatService(
        admin_user_ids=settings.admin_user_ids,
        users=users_repo,
        redis=redis,
        rate=rate_lc,
        user_rate_per_minute=settings.user_message_rate_per_minute,
        admin_rate_per_minute=settings.admin_reply_rate_per_minute,
    )

    broadcast_service = BroadcastService(
        redis=redis,
        broadcasts=broadcasts_repo,
        users=users_repo,
        queue_key=settings.redis_broadcast_queue,
        chunk_size=settings.broadcast_chunk_size,
    )

    retention = RetentionService(redis)
    livestream = LivestreamService(redis, prefix=settings.redis_livestream_prefix)

    user_svc = UserService(pool)

    application = (
        Application.builder()
        .token(settings.bot_token.get_secret_value())
        .concurrent_updates(True)
        .build()
    )

    application.bot_data["settings"] = settings
    application.bot_data["redis"] = redis
    application.bot_data["repos"] = {
        "admins": admins,
        "users": users_repo,
        "broadcasts": broadcasts_repo,
        "scheduled": scheduled_repo,
        "settings": settings_repo,
        "onboarding": onboarding_repo,
    }
    fsm = AdminFsm(redis, settings.redis_fsm_prefix)

    application.bot_data["services"] = {
        "users": user_svc,
        "live_chat": live_chat,
        "broadcast": broadcast_service,
        "retention": retention,
        "livestream": livestream,
        "fsm": fsm,
    }

    register_handlers(application, settings)
    return application


def create_redis_client(settings: Settings) -> Redis:
    """Shared Redis client factory."""
    return create_redis(settings.redis_url)
