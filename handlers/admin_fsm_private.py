"""Process admin wizard replies in private chat (before forwarding to support inbox)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

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
    STATE_LS_WAIT_MANUAL_URL,
    STATE_LS_WAIT_TEMPLATE,
    STATE_OD_WAIT_BODY,
    STATE_OD_WAIT_KB,
    STATE_RM_WAIT_BODY,
    STATE_RM_WAIT_HOURS,
    STATE_RM_WAIT_KB,
    STATE_SCH_WAIT_BODY,
    STATE_SCH_WAIT_FIRST_HOURS,
    STATE_SCH_WAIT_KB,
    STATE_SCH_WAIT_REPEAT_HOURS,
    STATE_WM_BATCH,
    STATE_WM_WAIT,
    STATE_WM_WAIT_KB,
    AdminFsm,
)
from utils.retention_display import format_retention_delay_human
from utils.telegram_urls import normalize_manual_live_url
from utils.keyboard_json import markup_from_json
from utils.message_serializer import message_to_payload

logger = logging.getLogger(__name__)

OPTIONAL_LINK_KB_PROMPT = (
    "Optional **link buttons** — paste a JSON array of button rows, or send `/skip`.\n"
    "Example:\n`[[{\"text\":\"Join\",\"url\":\"https://t.me/yourchannel\"}]]`"
)


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
                "**Step 2/4 — repeat**\n\n"
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
                "**Step 3/4 — message**\n\n"
                "Send the **broadcast** (text, photo, poll, etc.). "
                "Next you can add **link buttons** (JSON) or `/skip`."
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
                    "state": STATE_SCH_WAIT_KB,
                    "run_at": run_iso,
                    "repeat_hours": st.get("repeat_hours"),
                    "sched_message": pl,
                    "sched_created_by": uid,
                },
            )
            await msg.reply_text(OPTIONAL_LINK_KB_PROMPT, parse_mode="Markdown")
            raise ApplicationHandlerStop

        if state == STATE_SCH_WAIT_KB:
            run_iso = st.get("run_at")
            if not run_iso:
                await fsm.clear(uid)
                raise ApplicationHandlerStop
            run_at = datetime.fromisoformat(run_iso)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            raw = (msg.text or "").strip()
            pl = dict(st.get("sched_message") or {})
            if raw.lower() not in ("/skip", "skip"):
                try:
                    rows = json.loads(raw)
                    if not isinstance(rows, list):
                        raise ValueError("expected array")
                    if markup_from_json(rows) is None and rows:
                        raise ValueError("could not build markup")
                    pl["inline_keyboard"] = rows
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    await msg.reply_text(f"Invalid JSON: {e}")
                    raise ApplicationHandlerStop
            uid_cb = int(st.get("sched_created_by") or uid)
            payload: dict = {
                "mode": "broadcast_enqueue",
                "created_by": uid_cb,
                "message": pl,
            }
            repeat = st.get("repeat_hours")
            if repeat is not None and float(repeat) > 0:
                payload["interval_hours"] = float(repeat)
            jid = await scheduled_repo.create_job(
                created_by=uid_cb,
                run_at=run_at,
                payload=payload,
            )
            await fsm.clear(uid)
            note = ""
            if payload.get("interval_hours"):
                note = f" Repeats every **{payload['interval_hours']}** hour(s)."
            await msg.reply_text(
                f"⏱ Scheduled job `#{jid}` — first run about `{run_at.isoformat()}` UTC.{note}",
                parse_mode="Markdown",
            )
            await settings_repo.audit_log("INFO", "scheduler", f"job {jid}", {"admin": uid})
            raise ApplicationHandlerStop

        if state == STATE_WM_BATCH:
            pl = message_to_payload(msg)
            pending = list(st.get("pending") or [])
            pending.append(pl)
            await fsm.set(uid, {"state": STATE_WM_BATCH, "pending": pending})
            await msg.reply_text(
                f"Step `{len(pending)}` queued. Send more messages or `/done`.\n\n"
                "Tip: link buttons are not added in batch mode — use **Add step** once per step "
                "to paste JSON after the message."
            )
            raise ApplicationHandlerStop

        if state == STATE_WM_WAIT:
            pl = message_to_payload(msg)
            steps = await settings_repo.list_welcome_steps()
            mx = max((int(s.get("step_order") or 0) for s in steps), default=0)
            next_order = mx + 1
            await fsm.set(
                uid,
                {"state": STATE_WM_WAIT_KB, "wm_step": next_order, "wm_payload": pl},
            )
            await msg.reply_text(OPTIONAL_LINK_KB_PROMPT, parse_mode="Markdown")
            raise ApplicationHandlerStop

        if state == STATE_WM_WAIT_KB:
            next_order = int(st["wm_step"])
            pl = dict(st.get("wm_payload") or {})
            raw = (msg.text or "").strip()
            if raw.lower() not in ("/skip", "skip"):
                try:
                    rows = json.loads(raw)
                    if not isinstance(rows, list):
                        raise ValueError("expected array")
                    if markup_from_json(rows) is None and rows:
                        raise ValueError("could not build markup")
                    pl["inline_keyboard"] = rows
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    await msg.reply_text(f"Invalid JSON: {e}")
                    raise ApplicationHandlerStop
            await settings_repo.upsert_welcome_step(next_order, pl)
            await fsm.clear(uid)
            await msg.reply_text(f"👋 Welcome step `{next_order}` saved.")
            await settings_repo.audit_log(
                "INFO", "welcome", f"step {next_order}", {"admin": uid}
            )
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
                    "state": STATE_OD_WAIT_KB,
                    "od_step": step_order,
                    "od_delay": delay,
                    "od_payload": pl,
                },
            )
            await msg.reply_text(OPTIONAL_LINK_KB_PROMPT, parse_mode="Markdown")
            raise ApplicationHandlerStop

        if state == STATE_OD_WAIT_KB:
            step_order = int(st["od_step"])
            delay = int(st.get("od_delay") or 3600)
            onboarding_repo = context.application.bot_data["repos"]["onboarding"]
            pl = dict(st.get("od_payload") or {})
            raw = (msg.text or "").strip()
            if raw.lower() not in ("/skip", "skip"):
                try:
                    rows = json.loads(raw)
                    if not isinstance(rows, list):
                        raise ValueError("expected array")
                    if markup_from_json(rows) is None and rows:
                        raise ValueError("could not build markup")
                    pl["inline_keyboard"] = rows
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    await msg.reply_text(f"Invalid JSON: {e}")
                    raise ApplicationHandlerStop
            await onboarding_repo.upsert_message(step_order, delay, pl)
            await fsm.clear(uid)
            await msg.reply_text(f"🌱 Onboarding step `{step_order}` message saved.")
            await settings_repo.audit_log("INFO", "onboarding", f"step {step_order}", {"admin": uid})
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
                "**Step 2/3 — message**\n\n"
                "Send the **come-back message** for this step "
                "(text, photo, video, etc.). Forwards are copied.\n\n"
                "Next: optional link buttons (JSON) or `/skip`."
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
                    "state": STATE_RM_WAIT_KB,
                    "delay": delay,
                    "rm_step": next_order,
                    "rm_payload": pl,
                },
            )
            await msg.reply_text(OPTIONAL_LINK_KB_PROMPT, parse_mode="Markdown")
            raise ApplicationHandlerStop

        if state == STATE_RM_WAIT_KB:
            delay = int(st["delay"])
            next_order = int(st["rm_step"])
            pl = dict(st.get("rm_payload") or {})
            raw = (msg.text or "").strip()
            if raw.lower() not in ("/skip", "skip"):
                try:
                    rows = json.loads(raw)
                    if not isinstance(rows, list):
                        raise ValueError("expected array")
                    if markup_from_json(rows) is None and rows:
                        raise ValueError("could not build markup")
                    pl["inline_keyboard"] = rows
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    await msg.reply_text(f"Invalid JSON: {e}")
                    raise ApplicationHandlerStop
            await settings_repo.upsert_retention_step(next_order, delay, pl)
            await fsm.clear(uid)
            label = format_retention_delay_human(delay)
            await msg.reply_text(
                f"♻️ Come-back step `{next_order}` saved — **{label}** before this step is sent."
            )
            await settings_repo.audit_log(
                "INFO", "retention", f"step {next_order}", {"admin": uid}
            )
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
