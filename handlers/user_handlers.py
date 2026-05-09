"""End-user handlers: /start, message collection, welcome flow."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from database.repositories.settings_repo import SettingsRepository
from middlewares.admin_auth import require_admin
from services.outbound_sender import send_from_payload
from services.user_service import UserService

logger = logging.getLogger(__name__)


def _substitute_name(text: str, name: str) -> str:
    return text.replace("{name}", name or "")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect user and run welcome sequence."""
    if update.effective_user is None:
        return
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    user_svc: UserService = context.application.bot_data["services"]["users"]
    await user_svc.ingest_from_update(update)

    steps = await settings_repo.list_welcome_steps()
    name = update.effective_user.first_name or update.effective_user.full_name or ""
    for row in sorted(steps, key=lambda r: int(r.get("step_order") or 0)):
        payload = dict(row.get("payload") or {})
        if payload.get("kind") == "text" and isinstance(payload.get("text"), str):
            payload["text"] = _substitute_name(payload["text"], name)
        try:
            await send_from_payload(context.bot, chat_id=update.effective_chat.id, payload=payload)
        except Exception:
            logger.exception("Welcome step failed")

    if update.message:
        await update.message.reply_text(
            "👋 You're registered. Use /help if this bot serves your community."
        )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear admin FSM / draft (safe no-op for normal users)."""
    if update.effective_user is None or update.message is None:
        return
    fsm = context.application.bot_data["services"]["fsm"]
    uid = update.effective_user.id
    await fsm.clear(uid)
    await fsm.clear_draft_broadcast(uid)
    await update.message.reply_text("Cancelled.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Community bot: broadcasts, support forwarding, and channel tools.\n"
            "Admins: /admin — wizards: /cancel"
        )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    from keyboards.admin_panel import main_menu

    if update.message:
        await update.message.reply_text("🔧 Admin panel", reply_markup=main_menu())


async def any_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect stats + forward to admin inbox (non-commands)."""
    if update.effective_user is None:
        return
    uid = update.effective_user.id
    user_svc: UserService = context.application.bot_data["services"]["users"]
    await user_svc.ingest_from_update(update, increment_messages=True)

    admins_repo = context.application.bot_data["repos"]["admins"]
    settings = context.application.bot_data["settings"]
    if uid in settings.admin_user_ids or await admins_repo.is_admin(uid):
        return

    lc = context.application.bot_data["services"]["live_chat"]
    if update.message:
        await lc.forward_user_message(context.bot, update.message)


async def admin_inbox_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route native replies from admin inbox back to users."""
    if update.message is None:
        return
    lc = context.application.bot_data["services"]["live_chat"]
    await lc.relay_admin_reply(context.bot, update.message)


async def any_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track inline interactions for analytics + route admin panel."""
    if update.effective_user is None:
        return
    user_svc: UserService = context.application.bot_data["services"]["users"]
    await user_svc.ingest_user(update.effective_user)

    data = update.callback_query.data if update.callback_query else ""
    if data.startswith("adm:"):
        from handlers.admin_callbacks import route_admin_callback

        await route_admin_callback(update, context)
        return

    if update.callback_query:
        await update.callback_query.answer()


