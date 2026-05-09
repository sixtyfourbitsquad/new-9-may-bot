"""Admin authorization helpers for handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from database.repositories.admins import AdminRepository


async def is_admin(update: Update, admins: AdminRepository) -> bool:
    """Return True if the acting user is registered as admin."""
    user = update.effective_user
    if user is None:
        return False
    return await admins.is_admin(user.id)


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Use with early return in handlers; sends denial when unauthorized."""
    admins: AdminRepository = context.application.bot_data["repos"]["admins"]
    ok = await is_admin(update, admins)
    if ok:
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Admin only.")
    return False
