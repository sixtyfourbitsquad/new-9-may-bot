"""Join-request hook: welcome DM, stats, optional auto-approve."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from database.repositories.settings_repo import SettingsRepository
from services.user_service import UserService
from services.welcome_flow import send_welcome_sequence

logger = logging.getLogger(__name__)


async def on_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jq = update.chat_join_request
    if jq is None or jq.from_user is None:
        return

    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    user_svc: UserService = context.application.bot_data["services"]["users"]
    users_repo = context.application.bot_data["repos"]["users"]

    ch = await settings_repo.get_channel_settings()
    monitored = ch.get("monitored_chat_id")
    if monitored is None or int(jq.chat.id) != int(monitored):
        return

    await settings_repo.increment_join_requests_total()
    await user_svc.ingest_user(jq.from_user, source_channel=str(monitored))
    await users_repo.log_activity(
        jq.from_user.id,
        "join_request",
        {"chat": int(monitored)},
    )

    name = jq.from_user.first_name or jq.from_user.full_name or ""
    redis = context.application.bot_data["redis"]
    await send_welcome_sequence(
        context.bot,
        chat_id=jq.from_user.id,
        display_name=name,
        settings_repo=settings_repo,
        redis=redis,
    )

    if ch.get("auto_approve_join_requests"):
        try:
            await context.bot.approve_chat_join_request(
                chat_id=jq.chat.id,
                user_id=jq.from_user.id,
            )
        except Exception:
            logger.exception(
                "approve_chat_join_request failed chat=%s user=%s",
                jq.chat.id,
                jq.from_user.id,
            )
