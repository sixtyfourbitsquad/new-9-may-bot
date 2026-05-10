"""Process admin wizard replies in private chat (before forwarding to support inbox)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
    STATE_CH_WAIT_ID,
    STATE_COLLECT_LINK_BUTTONS,
    STATE_LS_WAIT_MANUAL_URL,
    STATE_LS_WAIT_TEMPLATE,
    STATE_OD_WAIT_BODY,
    STATE_RM_WAIT_BODY,
    STATE_RM_WAIT_HOURS,
    STATE_SCH_WAIT_BODY,
    STATE_SCH_WAIT_FIRST_HOURS,
    STATE_SCH_WAIT_REPEAT_HOURS,
    STATE_WM_BATCH,
    STATE_WM_WAIT,
    AdminFsm,
)
from utils.retention_display import format_retention_delay_human
from utils.telegram_urls import normalize_manual_live_url
from utils.keyboard_json import markup_from_json
from utils.message_serializer import message_to_payload

logger = logging.getLogger(__name__)

COLLECT_TARGET_WELCOME = "welcome"
COLLECT_TARGET_ONBOARDING = "onboarding"
COLLECT_TARGET_RETENTION = "retention"
COLLECT_TARGET_SCHEDULED = "scheduled"


def _prompt_link_button_wizard() -> str:
    return (
        "**Optional buttons**\n\n"
        "Send the **first button label** (the text people tap), or **`/skip`** for no buttons.\n\n"
        "For each button you add: you’ll send the **label**, then the **link** "
        "(`https://...` or `t.me/...`). When you’re finished adding buttons, send **`/done`**."
    )


async def _complete_link_button_wizard(
    uid: int,
    msg,
    context: ContextTypes.DEFAULT_TYPE,
    st: dict[str, Any],
    final_payload: dict[str, Any],
) -> None:
    fsm: AdminFsm = context.application.bot_data["services"]["fsm"]
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    scheduled_repo: ScheduledRepository = context.application.bot_data["repos"]["scheduled"]
    onboarding_repo: OnboardingRepository = context.application.bot_data["repos"]["onboarding"]

    target = str(st.get("collect_for") or "")
    await fsm.clear(uid)

    if target == COLLECT_TARGET_WELCOME:
        step = int(st["wm_step"])
        await settings_repo.upsert_welcome_step(step, final_payload)
        await msg.reply_text(f"👋 Welcome step `{step}` saved.")
        await settings_repo.audit_log("INFO", "welcome", f"step {step}", {"admin": uid})
        return

    if target == COLLECT_TARGET_ONBOARDING:
        step_order = int(st["od_step"])
        delay = int(st.get("od_delay") or 3600)
        await onboarding_repo.upsert_message(step_order, delay, final_payload)
        await msg.reply_text(f"🌱 Onboarding step `{step_order}` message saved.")
        await settings_repo.audit_log(
            "INFO", "onboarding", f"step {step_order}", {"admin": uid}
        )
        return

    if target == COLLECT_TARGET_RETENTION:
        step = int(st["rm_step"])
        delay = int(st["rm_delay"])
        await settings_repo.upsert_retention_step(step, delay, final_payload)
        label_h = format_retention_delay_human(delay)
        await msg.reply_text(
            f"♻️ Come-back step `{step}` saved — **{label_h}** before this step is sent."
        )
        await settings_repo.audit_log("INFO", "retention", f"step {step}", {"admin": uid})
        return

    if target == COLLECT_TARGET_SCHEDULED:
        run_iso = st.get("run_at")
        if not run_iso:
            await msg.reply_text("Internal error: missing schedule; try again.")
            return
        run_at = datetime.fromisoformat(str(run_iso))
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        payload = {
            "mode": "broadcast_enqueue",
            "created_by": uid,
            "message": final_payload,
        }
        repeat = st.get("repeat_hours")
        if repeat is not None and float(repeat) > 0:
            payload["interval_hours"] = float(repeat)
        jid = await scheduled_repo.create_job(
            created_by=uid,
            run_at=run_at,
            payload=payload,
        )
        note = ""
        if payload.get("interval_hours"):
            note = f" Repeats every **{payload['interval_hours']}** hour(s)."
        await msg.reply_text(
            f"⏱ Scheduled job `#{jid}` — first run about `{run_at.isoformat()}` UTC.{note}",
            parse_mode="Markdown",
        )
        await settings_repo.audit_log("INFO", "scheduler", f"job {jid}", {"admin": uid})
        return

    await msg.reply_text("Could not save — unknown step. Try /cancel and start again.")


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

        if state == STATE_SCH_WAIT_FIRST_HOURS:
            raw = (msg.text or "").strip().replace(",", ".")
            try:
                first_hours = float(raw)
            except ValueError:
                await msg.reply_text("Send a number: hours **from now** until the first send (e.g. `2`, `0.5`).")
                raise ApplicationHandlerStop
            if first_hours < 0:
                await msg.reply_text("Hours cannot be negative.")
                raise ApplicationHandlerStop
            await fsm.set(uid, {"state": STATE_SCH_WAIT_REPEAT_HOURS, "first_hours": first_hours})
            await msg.reply_text(
                "**Step 2/3 — repeat**\n\n"
                "Send hours between each following broadcast, or `0` for **once only**.\n"
                "Example: `3` means this broadcast runs **every 3 hours** after the first."
            )
            raise ApplicationHandlerStop

        if state == STATE_SCH_WAIT_REPEAT_HOURS:
            raw = (msg.text or "").strip().lower().replace(",", ".")
            if raw in ("", "no", "once", "n"):
                repeat_h: float | None = None
            else:
                try:
                    rh = float(raw)
                except ValueError:
                    await msg.reply_text("Send a number of hours, or `0` / `no` for a single run.")
                    raise ApplicationHandlerStop
                if rh < 0:
                    await msg.reply_text("Hours cannot be negative.")
                    raise ApplicationHandlerStop
                repeat_h = rh if rh > 0 else None
            first_hours = float(st.get("first_hours") or 0)
            run_at = datetime.now(timezone.utc) + timedelta(hours=first_hours)
            await fsm.set(
                uid,
                {
                    "state": STATE_SCH_WAIT_BODY,
                    "run_at": run_at.isoformat(),
                    "repeat_hours": repeat_h,
                },
            )
            await msg.reply_text(
                "**Step 3/3 — message**\n\n"
                "Send the **broadcast** (text, photo, poll, etc.)."
            )
            raise ApplicationHandlerStop

        if state == STATE_COLLECT_LINK_BUTTONS:
            phase = str(st.get("btn_phase") or "label")
            rows = list(st.get("btn_rows") or [])
            base_payload = dict(st.get("base_payload") or {})
            raw = (msg.text or "").strip()
            low = raw.lower()

            if phase == "label":
                if low in ("/skip", "skip"):
                    if rows:
                        await msg.reply_text(
                            "You already added buttons. Send **/done** to save, "
                            "or send another **button label**."
                        )
                        raise ApplicationHandlerStop
                    await _complete_link_button_wizard(uid, msg, context, st, base_payload)
                    raise ApplicationHandlerStop

                if low in ("/done", "done"):
                    if not rows:
                        await msg.reply_text(
                            "No buttons yet. Send a **button label**, or **`/skip`** for no buttons."
                        )
                        raise ApplicationHandlerStop
                    out_pl = dict(base_payload)
                    out_pl["inline_keyboard"] = rows
                    await _complete_link_button_wizard(uid, msg, context, st, out_pl)
                    raise ApplicationHandlerStop

                if not raw:
                    await msg.reply_text("Send the text for the button (what users see).")
                    raise ApplicationHandlerStop

                nst = dict(st)
                nst["btn_phase"] = "url"
                nst["pending_btn_label"] = raw
                await fsm.set(uid, nst)
                await msg.reply_text(
                    "Now send the **link** for this button (`https://...` or `t.me/...`)."
                )
                raise ApplicationHandlerStop

            if phase == "url":
                url = normalize_manual_live_url(raw)
                if not url:
                    await msg.reply_text(
                        "Could not read that link. Send `https://...` or `t.me/...`"
                    )
                    raise ApplicationHandlerStop
                label = str(st.get("pending_btn_label") or "").strip() or "Open"
                new_rows = list(rows)
                new_rows.append([{"text": label, "url": url}])
                nst = dict(st)
                nst["btn_phase"] = "label"
                nst["pending_btn_label"] = None
                nst["btn_rows"] = new_rows
                await fsm.set(uid, nst)
                await msg.reply_text(
                    f"Added **{label}**.\n\n"
                    "Send another **button label**, or **`/done`** to finish."
                )
                raise ApplicationHandlerStop

        if state == STATE_SCH_WAIT_BODY:
            run_iso = st.get("run_at")
            if not run_iso:
                await fsm.clear(uid)
                raise ApplicationHandlerStop
            pl = message_to_payload(msg)
            await fsm.set(
                uid,
                {
                    "state": STATE_COLLECT_LINK_BUTTONS,
                    "collect_for": COLLECT_TARGET_SCHEDULED,
                    "btn_phase": "label",
                    "btn_rows": [],
                    "base_payload": pl,
                    "run_at": run_iso,
                    "repeat_hours": st.get("repeat_hours"),
                },
            )
            await msg.reply_text(_prompt_link_button_wizard(), parse_mode="Markdown")
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
            next_order = mx + 1
            await fsm.set(
                uid,
                {
                    "state": STATE_COLLECT_LINK_BUTTONS,
                    "collect_for": COLLECT_TARGET_WELCOME,
                    "btn_phase": "label",
                    "btn_rows": [],
                    "base_payload": pl,
                    "wm_step": next_order,
                },
            )
            await msg.reply_text(_prompt_link_button_wizard(), parse_mode="Markdown")
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
            await fsm.set(
                uid,
                {
                    "state": STATE_COLLECT_LINK_BUTTONS,
                    "collect_for": COLLECT_TARGET_ONBOARDING,
                    "btn_phase": "label",
                    "btn_rows": [],
                    "base_payload": pl,
                    "od_step": step_order,
                    "od_delay": delay,
                },
            )
            await msg.reply_text(_prompt_link_button_wizard(), parse_mode="Markdown")
            raise ApplicationHandlerStop

        if state == STATE_RM_WAIT_HOURS:
            raw = (msg.text or "").strip().replace(",", ".")
            try:
                hours = float(raw)
            except ValueError:
                await msg.reply_text("Send a number of **hours** (e.g. `2`, `0.5` for 30 min).")
                raise ApplicationHandlerStop
            if hours < 0:
                await msg.reply_text("Hours cannot be negative.")
                raise ApplicationHandlerStop
            delay = int(round(hours * 3600))
            await fsm.set(uid, {"state": STATE_RM_WAIT_BODY, "delay": delay})
            await msg.reply_text(
                "**Step 2/2 — message**\n\n"
                "Send the **come-back message** for this step "
                "(text, photo, video, etc.). Forwards are copied.\n\n"
                "Then you can add **link buttons** (text → link each time), or **`/skip`**."
            )
            raise ApplicationHandlerStop

        if state == STATE_RM_WAIT_BODY:
            delay = int(st["delay"]) if "delay" in st else 3600
            pl = message_to_payload(msg)
            steps = await settings_repo.list_retention_steps()
            mx = max((int(s.get("step_order") or 0) for s in steps), default=0)
            next_order = mx + 1
            await fsm.set(
                uid,
                {
                    "state": STATE_COLLECT_LINK_BUTTONS,
                    "collect_for": COLLECT_TARGET_RETENTION,
                    "btn_phase": "label",
                    "btn_rows": [],
                    "base_payload": pl,
                    "rm_step": next_order,
                    "rm_delay": delay,
                },
            )
            await msg.reply_text(_prompt_link_button_wizard(), parse_mode="Markdown")
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
