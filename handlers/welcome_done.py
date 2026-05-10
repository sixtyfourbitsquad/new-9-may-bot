"""Finish welcome batch wizard with /done."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from database.repositories.settings_repo import SettingsRepository
from middlewares.admin_auth import require_admin
from services.admin_fsm import STATE_WM_BATCH, AdminFsm


async def cmd_welcome_batch_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not await require_admin(update, context):
        return

    uid = update.effective_user.id
    fsm: AdminFsm = context.application.bot_data["services"]["fsm"]
    st = await fsm.get(uid)
    if not st or str(st.get("state") or "") != STATE_WM_BATCH:
        await update.message.reply_text("No welcome batch in progress. Use Welcome → Batch add.")
        return

    pending_raw = st.get("pending")
    pending: list = list(pending_raw) if isinstance(pending_raw, list) else []

    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    if not pending:
        await fsm.clear(uid)
        await update.message.reply_text("Nothing to save — send messages first, then /done.")
        return

    steps = await settings_repo.list_welcome_steps()
    mx = max((int(s.get("step_order") or 0) for s in steps), default=0)
    for i, pl in enumerate(pending, start=1):
        await settings_repo.upsert_welcome_step(mx + i, pl)

    await fsm.clear(uid)
    await update.message.reply_text(f"Saved `{len(pending)}` welcome steps.")
    await settings_repo.audit_log(
        "INFO",
        "welcome",
        f"batch {len(pending)} steps",
        {"admin": uid},
    )
