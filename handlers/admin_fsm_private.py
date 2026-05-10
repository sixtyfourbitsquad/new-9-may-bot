"""Process admin wizard replies in private chat (before forwarding to support inbox)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from database.repositories.scheduled import ScheduledRepository
from database.repositories.settings_repo import SettingsRepository
from models.domain import AdminRole
from database.repositories.onboarding_repo import OnboardingRepository
from services.admin_fsm import (
    STATE_AD_WAIT_ID,
    STATE_BC_WAIT_BUTTONS_JSON,
    STATE_BC_WAIT_MSG,
    STATE_BTN_WAIT_JSON,
    STATE_BTN_WAIT_NAME,
    STATE_CH_WAIT_ID,
    STATE_LS_WAIT_MANUAL_URL,
    STATE_LS_WAIT_TEMPLATE,
    STATE_OD_WAIT_BODY,
    STATE_RM_WAIT_BODY,
    STATE_RM_WAIT_DELAY,
    STATE_SCH_WAIT_BODY,
    STATE_SCH_WAIT_TIME,
    STATE_WM_BATCH,
    STATE_WM_WAIT,
    AdminFsm,
)
from utils.datetime_parse import parse_iso_utc
from utils.telegram_urls import normalize_manual_live_url
from utils.keyboard_json import markup_from_json
from utils.message_serializer import message_to_payload

logger = logging.getLogger(__name__)


def _broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👁 Test on myself", callback_data="adm:bc:pr"),
                InlineKeyboardButton("📨 Send to everyone", callback_data="adm:bc:sd"),
            ],
            [
                InlineKeyboardButton("⌨️ Add link buttons (advanced)", callback_data="adm:bc:kb"),
                InlineKeyboardButton("❌ Discard", callback_data="adm:bc:xx"),
            ],
        ]
    )


async def _is_owner(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    role = await context.application.bot_data["repos"]["admins"].get_role(uid)
    return role == AdminRole.OWNER


async def admin_fsm_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle private non-command messages when an admin has an active FSM state."""
    if update.effective_user is None or update.message is None:
        return

    uid = update.effective_user.id
    admins_repo = context.application.bot_data["repos"]["admins"]
    settings_cfg = context.application.bot_data["settings"]
    if uid not in settings_cfg.admin_user_ids and not await admins_repo.is_admin(uid):
        return

    fsm: AdminFsm = context.application.bot_data["services"]["fsm"]
    st = await fsm.get(uid)
    if not st:
        return

    state = str(st.get("state") or "")
    msg = update.message
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    scheduled_repo: ScheduledRepository = context.application.bot_data["repos"]["scheduled"]

    try:
        if state == STATE_BC_WAIT_MSG:
            payload = message_to_payload(msg)
            await fsm.set_draft_broadcast(uid, payload)
            await fsm.clear(uid)
            await msg.reply_text(
                "Saved. What do you want to do next?",
                reply_markup=_broadcast_confirm_kb(),
            )
            raise ApplicationHandlerStop

        if state == STATE_BC_WAIT_BUTTONS_JSON:
            text = (msg.text or "").strip()
            try:
                rows = json.loads(text)
                if not isinstance(rows, list):
                    raise ValueError("must be array of rows")
                mk = markup_from_json(rows)
                if mk is None and rows:
                    raise ValueError("could not build markup")
                draft = await fsm.get_draft_broadcast(uid)
                if not draft:
                    await msg.reply_text("Draft expired. Start again from Broadcasts → New.")
                    await fsm.clear(uid)
                    raise ApplicationHandlerStop
                draft["inline_keyboard"] = rows
                await fsm.set_draft_broadcast(uid, draft)
                await fsm.clear(uid)
                await msg.reply_text("Buttons merged.", reply_markup=_broadcast_confirm_kb())
            except (json.JSONDecodeError, ValueError) as e:
                await msg.reply_text(f"Invalid JSON: {e}")
            raise ApplicationHandlerStop

        if state == STATE_SCH_WAIT_TIME:
            try:
                run_at = parse_iso_utc(msg.text or "")
            except ValueError:
                await msg.reply_text("Invalid datetime. Use ISO UTC e.g. `2026-05-10T15:00:00Z`")
                raise ApplicationHandlerStop
            await fsm.set(
                uid,
                {"state": STATE_SCH_WAIT_BODY, "run_at": run_at.isoformat()},
            )
            await msg.reply_text("Now send the message content (text, photo, poll, etc.).")
            raise ApplicationHandlerStop

        if state == STATE_SCH_WAIT_BODY:
            run_iso = st.get("run_at")
            if not run_iso:
                await fsm.clear(uid)
                raise ApplicationHandlerStop
            run_at = datetime.fromisoformat(run_iso)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            pl = message_to_payload(msg)
            jid = await scheduled_repo.create_job(
                created_by=uid,
                run_at=run_at,
                payload={
                    "mode": "broadcast_enqueue",
                    "created_by": uid,
                    "message": pl,
                },
            )
            await fsm.clear(uid)
            await msg.reply_text(f"⏱ Scheduled job `#{jid}` saved (fires at run time).")
            await settings_repo.audit_log("INFO", "scheduler", f"job {jid}", {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_WM_BATCH:
            pl = message_to_payload(msg)
            pending = list(st.get("pending") or [])
            pending.append(pl)
            await fsm.set(uid, {"state": STATE_WM_BATCH, "pending": pending})
            await msg.reply_text(
                f"Step `{len(pending)}` queued. Send more messages or `/done`."
            )
            raise ApplicationHandlerStop

        if state == STATE_WM_WAIT:
            pl = message_to_payload(msg)
            steps = await settings_repo.list_welcome_steps()
            mx = max((int(s.get("step_order") or 0) for s in steps), default=0)
            await settings_repo.upsert_welcome_step(mx + 1, pl)
            await fsm.clear(uid)
            await msg.reply_text(f"👋 Welcome step `{mx + 1}` saved.")
            await settings_repo.audit_log("INFO", "welcome", f"step {mx+1}", {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_OD_WAIT_BODY:
            step_order = int(st.get("od_step") or 0)
            if step_order <= 0:
                await fsm.clear(uid)
                raise ApplicationHandlerStop
            onboarding_repo: OnboardingRepository = context.application.bot_data["repos"]["onboarding"]
            rows = await onboarding_repo.list_messages()
            delay = 3600
            for r in rows:
                if int(r.get("step_order") or 0) == step_order:
                    delay = int(r.get("delay_seconds") or delay)
                    break
            pl = message_to_payload(msg)
            await onboarding_repo.upsert_message(step_order, delay, pl)
            await fsm.clear(uid)
            await msg.reply_text(f"🌱 Onboarding step `{step_order}` message saved.")
            await settings_repo.audit_log("INFO", "onboarding", f"step {step_order}", {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_RM_WAIT_DELAY:
            try:
                delay = int((msg.text or "").strip())
                if delay < 0:
                    raise ValueError
            except ValueError:
                await msg.reply_text(
                    "Send a whole number of seconds (e.g. `0` for fast, `3600` for 1 hour)."
                )
                raise ApplicationHandlerStop
            await fsm.set(uid, {"state": STATE_RM_WAIT_BODY, "delay": delay})
            await msg.reply_text(
                "Now send the **come-back message** for this step "
                "(text, photo, video, etc.). Forwards are copied."
            )
            raise ApplicationHandlerStop

        if state == STATE_RM_WAIT_BODY:
            delay = int(st.get("delay") or 3600)
            pl = message_to_payload(msg)
            steps = await settings_repo.list_retention_steps()
            mx = max((int(s.get("step_order") or 0) for s in steps), default=0)
            await settings_repo.upsert_retention_step(mx + 1, delay, pl)
            await fsm.clear(uid)
            await msg.reply_text(
                f"♻️ Come-back step `{mx + 1}` saved "
                f"(wait **{delay}s** before this step is sent)."
            )
            await settings_repo.audit_log("INFO", "retention", f"step {mx+1}", {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_BTN_WAIT_NAME:
            name = (msg.text or "").strip()
            if not name:
                await msg.reply_text("Name cannot be empty.")
                raise ApplicationHandlerStop
            await fsm.set(uid, {"state": STATE_BTN_WAIT_JSON, "preset_name": name})
            await msg.reply_text("Send inline keyboard JSON (array of rows).")
            raise ApplicationHandlerStop

        if state == STATE_BTN_WAIT_JSON:
            name = str(st.get("preset_name") or "preset")
            try:
                rows = json.loads(msg.text or "")
                if not isinstance(rows, list):
                    raise ValueError("expected array")
                markup_from_json(rows)
            except ApplicationHandlerStop:
                raise
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                await msg.reply_text(f"Invalid keyboard JSON: {e}")
                raise ApplicationHandlerStop
            await settings_repo.save_inline_preset(name, rows)
            await fsm.clear(uid)
            await msg.reply_text(f"🔘 Preset `{name}` saved.")
            await settings_repo.audit_log("INFO", "buttons", name, {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_CH_WAIT_ID:
            cid: int | None = None
            fo = msg.forward_origin
            if fo is not None:
                ch_obj = getattr(fo, "chat", None) or getattr(fo, "sender_chat", None)
                if ch_obj is not None and getattr(ch_obj, "id", None) is not None:
                    cid = int(ch_obj.id)
            if cid is None:
                raw = (msg.text or "").strip()
                try:
                    cid = int(raw)
                except ValueError:
                    await msg.reply_text(
                        "Forward a post from the channel here, or paste numeric chat id "
                        "(negative for channels/supergroups)."
                    )
                    raise ApplicationHandlerStop
            await settings_repo.set_monitored_chat(cid)
            await fsm.clear(uid)
            await msg.reply_text(f"📺 Monitored chat set to `{cid}`.")
            await settings_repo.audit_log("INFO", "channel", str(cid), {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_LS_WAIT_TEMPLATE:
            await settings_repo.update_livestream(notification_template=msg.text or "")
            await fsm.clear(uid)
            await msg.reply_text("📡 Livestream template updated.")
            raise ApplicationHandlerStop

        if state == STATE_LS_WAIT_MANUAL_URL:
            normalized = normalize_manual_live_url(msg.text or "")
            if not normalized:
                await msg.reply_text(
                    "Could not parse a URL. Send `https://t.me/...` or `t.me/...` "
                    "(invite or public link)."
                )
                raise ApplicationHandlerStop
            await settings_repo.update_livestream(manual_live_url=normalized)
            await fsm.clear(uid)
            await msg.reply_text(
                "Join live link saved. Livestream alerts will include a Join now button."
            )
            await settings_repo.audit_log(
                "INFO", "livestream", "manual_live_url set", {"admin": uid}
            )
            raise ApplicationHandlerStop

        if state == STATE_AD_WAIT_ID:
            if not await _is_owner(context, uid):
                await msg.reply_text("⛔ Owners only.")
                await fsm.clear(uid)
                raise ApplicationHandlerStop
            try:
                new_id = int((msg.text or "").strip())
            except ValueError:
                await msg.reply_text("Send numeric Telegram user id.")
                raise ApplicationHandlerStop
            await admins_repo.add_admin(new_id, AdminRole.SUPPORT, uid)
            await fsm.clear(uid)
            await msg.reply_text(f"👮 Added `{new_id}` as support.")
            await settings_repo.audit_log("INFO", "admins", f"add {new_id}", {"admin": uid})
            raise ApplicationHandlerStop

    except ApplicationHandlerStop:
        raise
    except Exception:
        logger.exception("FSM handler error")
        await msg.reply_text("Error; try /cancel and retry.")
        raise ApplicationHandlerStop
