"""Monitored channel membership + livestream notifications."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from configs.settings import Settings
from database.repositories.settings_repo import SettingsRepository
from models.domain import BroadcastStatus
from services.broadcast_service import BroadcastService
from services.livestream_service import LivestreamService
from services.outbound_sender import merge_inline_keyboard
from services.retention_service import RetentionService
from services.welcome_flow import send_welcome_sequence
from utils.retention_display import format_retention_delay_human
from utils.telegram_urls import normalize_manual_live_url

logger = logging.getLogger(__name__)


def _public_live_watch_url(chat) -> str | None:
    """Telegram opens the channel live page at t.me/<username>/live for public chats."""
    username = getattr(chat, "username", None)
    if not username:
        return None
    return f"https://t.me/{username}/live"


def _append_fallback_url_to_payload(payload: dict[str, Any], url: str) -> None:
    """Ensure at least one tappable https URL in the body (some clients lose inline keyboards)."""
    if not url.strip():
        return
    kind = str(payload.get("kind") or "text")
    suffix = f"\n\n{url.strip()}"
    if kind == "text":
        payload["text"] = str(payload.get("text") or "").rstrip() + suffix
        return
    if kind in ("photo", "video", "animation", "audio", "voice", "document"):
        payload["caption"] = str(payload.get("caption") or "").rstrip() + suffix


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
    # Telegram sends chat_member when the bot itself joins as admin; never DM/welcome the bot id.
    if user.id == context.bot.id:
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
        name = user.first_name or user.full_name or ""
        redis = context.application.bot_data.get("redis")
        await send_welcome_sequence(
            context.bot,
            chat_id=user.id,
            display_name=name,
            settings_repo=settings_repo,
            redis=redis,
        )
        logger.info(
            "User joined monitored channel chat_id=%s user_id=%s (welcome sequence attempted)",
            monitored,
            user.id,
        )

    became_left = new.status in ("left", "kicked") and old.status not in ("left", "kicked")

    if became_left:
        logger.info(
            "Member left monitored channel chat=%s user_id=%s (%s -> %s)",
            monitored,
            user.id,
            old.status,
            new.status,
        )

    if not ch.get("retention_enabled", True):
        if became_left:
            logger.info(
                "Retention disabled in settings; not scheduling after leave user_id=%s",
                user.id,
            )
        return

    if not became_left:
        return

    rows = await settings_repo.list_retention_steps()
    rows_sorted = sorted(rows, key=lambda r: int(r.get("step_order") or 0))
    if not rows_sorted:
        logger.warning(
            "Retention: user left monitored chat but DB has no retention steps (user_id=%s)",
            user.id,
        )
        return
    retention: RetentionService = context.application.bot_data["services"]["retention"]
    delay0 = int(rows_sorted[0].get("delay_seconds") or 300)
    await retention.schedule_first_step(user.id, delay0)
    logger.info(
        "Retention step 1 scheduled user_id=%s delay_s=%s (%s until first DM) monitored=%s",
        user.id,
        delay0,
        format_retention_delay_human(delay0),
        monitored,
    )
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
    try:
        chat_for_links = await context.bot.get_chat(message.chat_id)
    except TelegramError:
        chat_for_links = message.chat

    invite = await live_svc.resolve_invite_link(context.bot, message.chat_id)
    watch_live = _public_live_watch_url(chat_for_links)
    manual = normalize_manual_live_url(ls_row.get("manual_live_url"))

    banner_payload = ls_row.get("banner_payload")
    button_payload = ls_row.get("button_payload")

    payload: dict[str, Any]
    if isinstance(banner_payload, dict) and banner_payload:
        payload = dict(banner_payload)
    else:
        payload = {"kind": "text", "text": template}

    extra_rows: list[list[dict[str, Any]]] = []
    button_row: list[dict[str, Any]] = []
    # Private channels: set admin "Join live link"; public may still use t.me/.../live + invite.
    if manual:
        button_row.append({"text": "Join now", "url": manual})
        if invite and invite.rstrip("/") != manual.rstrip("/"):
            button_row.append({"text": "Join channel", "url": invite})
    else:
        if watch_live:
            button_row.append({"text": "🔴 Watch live", "url": watch_live})
        if invite:
            button_row.append(
                {"text": "Join channel" if watch_live else "Join channel / live", "url": invite}
            )
    if button_row:
        extra_rows.append(button_row)
    if isinstance(button_payload, list):
        extra_rows.extend(list(button_payload))
    if extra_rows:
        payload = merge_inline_keyboard(payload, extra_rows=extra_rows)

    # Duplicate URL in message text only when we could not attach inline URL buttons
    # (e.g. invite export failed). If Join now / Watch live buttons exist, skip — avoids clutter.
    primary_link = manual or watch_live or invite
    if primary_link and not button_row:
        _append_fallback_url_to_payload(payload, primary_link)

    br = context.application.bot_data["repos"]["broadcasts"]
    bc_service: BroadcastService = context.application.bot_data["services"]["broadcast"]

    bid = await br.create_broadcast(
        created_by=0,
        payload=payload,
        status=BroadcastStatus.QUEUED,
    )
    await bc_service.enqueue_broadcast(bid)
    await settings_repo.audit_log("INFO", "livestream", f"Broadcast {bid} for chat {message.chat_id}")
