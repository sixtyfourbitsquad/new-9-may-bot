"""Monitored channel membership + livestream notifications."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from configs.settings import Settings
from database.repositories.settings_repo import SettingsRepository
from models.domain import BroadcastStatus
from services.broadcast_service import BroadcastService
from services.livestream_service import LivestreamService
from services.outbound_sender import merge_inline_keyboard
from services.retention_service import RetentionService

logger = logging.getLogger(__name__)


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track joins to monitored chat + trigger retention on leave."""
    if update.chat_member is None:
        return
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    user_svc = context.application.bot_data["services"]["users"]
    ch = await settings_repo.get_channel_settings()
    monitored = ch.get("monitored_chat_id")
    if monitored is None:
        return
    if int(update.chat_member.chat.id) != int(monitored):
        return
    new = update.chat_member.new_chat_member
    old = update.chat_member.old_chat_member
    user = update.chat_member.from_user
    if user is None:
        return

    joined = new.status in ("member", "administrator", "creator") and old.status in (
        "left",
        "kicked",
        "restricted",
    )
    if joined:
        await user_svc.ingest_user(user, source_channel=str(update.chat_member.chat.id))
        await context.application.bot_data["repos"]["users"].log_activity(
            user.id, "joined_monitored", {"chat": int(monitored)}
        )

    if not ch.get("retention_enabled", True):
        return

    became_left = new.status in ("left", "kicked") and old.status not in ("left", "kicked")
    if not became_left:
        return

    rows = await settings_repo.list_retention_steps()
    rows_sorted = sorted(rows, key=lambda r: int(r.get("step_order") or 0))
    if not rows_sorted:
        return
    retention: RetentionService = context.application.bot_data["services"]["retention"]
    delay0 = int(rows_sorted[0].get("delay_seconds") or 300)
    await retention.schedule_first_step(user.id, delay0)
    await context.application.bot_data["repos"]["users"].log_activity(
        user.id, "retention_scheduled", {"monitored": int(monitored)}
    )


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Video chat / livestream started in channel → broadcast notification."""
    message = update.channel_post
    if message is None:
        return
    if message.video_chat_started is None:
        return

    settings: Settings = context.application.bot_data["settings"]
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    ls_row = await settings_repo.get_livestream_settings()
    cooldown = int(ls_row.get("cooldown_seconds") or settings.livestream_cooldown_seconds)
    live_svc: LivestreamService = context.application.bot_data["services"]["livestream"]
    if not await live_svc.should_notify(message.chat_id, cooldown):
        logger.info("Livestream notify suppressed by cooldown for chat %s", message.chat_id)
        return

    template = str(ls_row.get("notification_template") or "🔴 LIVE STREAM STARTED! Join now!")
    invite = await live_svc.resolve_invite_link(context.bot, message.chat_id)

    banner_payload = ls_row.get("banner_payload")
    button_payload = ls_row.get("button_payload")

    payload: dict[str, Any]
    if isinstance(banner_payload, dict) and banner_payload:
        payload = dict(banner_payload)
    else:
        payload = {"kind": "text", "text": template}

    extra_rows: list[list[dict[str, Any]]] = []
    if invite:
        extra_rows.append([{"text": "Join channel / live", "url": invite}])
    if isinstance(button_payload, list):
        extra_rows.extend(list(button_payload))
    if extra_rows:
        payload = merge_inline_keyboard(payload, extra_rows=extra_rows)

    br = context.application.bot_data["repos"]["broadcasts"]
    bc_service: BroadcastService = context.application.bot_data["services"]["broadcast"]

    bid = await br.create_broadcast(
        created_by=0,
        payload=payload,
        status=BroadcastStatus.QUEUED,
    )
    await bc_service.enqueue_broadcast(bid)
    await settings_repo.audit_log("INFO", "livestream", f"Broadcast {bid} for chat {message.chat_id}")
