"""End-user handlers: /start, message collection, welcome flow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from database.repositories.onboarding_repo import OnboardingRepository
from database.repositories.settings_repo import SettingsRepository
from handlers.admin_callbacks import route_admin_callback
from middlewares.admin_auth import require_admin
from services.admin_fsm import STATE_COLLECT_LINK_BUTTONS, AdminFsm
from services.user_service import UserService
from services.welcome_flow import send_welcome_sequence

from handlers import admin_fsm_private, welcome_done

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect user and run welcome sequence."""
    if update.effective_user is None:
        return
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    user_svc: UserService = context.application.bot_data["services"]["users"]
    users_repo = context.application.bot_data["repos"]["users"]
    settings = context.application.bot_data["settings"]

    await user_svc.ingest_from_update(update)

    name = update.effective_user.first_name or update.effective_user.full_name or ""
    redis = context.application.bot_data["redis"]
    await send_welcome_sequence(
        context.bot,
        chat_id=update.effective_chat.id,
        display_name=name,
        settings_repo=settings_repo,
        redis=redis,
    )

    await users_repo.log_activity(update.effective_user.id, "welcome_completed", {})

    if settings.onboarding_drip_enabled:
        onboarding: OnboardingRepository = context.application.bot_data["repos"]["onboarding"]
        await onboarding.enqueue_for_user(update.effective_user.id, datetime.now(timezone.utc))


async def cmd_wizard_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip optional inline buttons (same as ⏭ Skip); commands reach here because FSM excludes COMMAND."""
    if update.effective_user is None or update.message is None:
        return
    if not await require_admin(update, context):
        return
    uid = update.effective_user.id
    handled = await admin_fsm_private.apply_collect_link_action(
        uid, context, "skip", update.message
    )
    if not handled:
        await update.message.reply_text(
            "Nothing to skip here. Use **Admin** when a step asks for optional buttons.",
            parse_mode="Markdown",
        )


async def cmd_done_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Link-button wizard /done vs welcome batch /done (STATE_WM_BATCH)."""
    if update.effective_user is None or update.message is None:
        return
    uid = update.effective_user.id
    fsm: AdminFsm = context.application.bot_data["services"]["fsm"]
    st = await fsm.get(uid)
    if st and str(st.get("state") or "") == STATE_COLLECT_LINK_BUTTONS:
        if not await require_admin(update, context):
            return
        handled = await admin_fsm_private.apply_collect_link_action(
            uid, context, "done", update.message
        )
        if handled:
            return
    await welcome_done.cmd_welcome_batch_done(update, context)


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
    """No reply — welcome/onboarding carry UX; keeps chat clean."""
    return


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    from keyboards.admin_panel import main_menu

    if update.message:
        await update.message.reply_text(
            "Control panel — tap a button:",
            reply_markup=main_menu(),
        )


async def any_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect stats + forward to admin inbox (non-commands)."""
    if update.effective_user is None:
        return
    uid = update.effective_user.id
    user_svc: UserService = context.application.bot_data["services"]["users"]
    await user_svc.ingest_from_update(update, increment_messages=True)

    settings = context.application.bot_data["settings"]
    if uid in settings.admin_user_ids:
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
        await route_admin_callback(update, context)
        return

    if update.callback_query:
        await update.callback_query.answer()


