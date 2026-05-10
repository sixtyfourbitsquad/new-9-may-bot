"""Inline admin panel — full CRUD / controls."""

from __future__ import annotations

import os
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

from configs.settings import Settings
from database.pool import get_pool
from database.repositories.settings_repo import SettingsRepository
from keyboards.admin_menus import (
    admins_menu,
    broadcasts_menu,
    buttons_menu,
    channel_live_menu,
    onboarding_menu,
    retention_menu,
    scheduled_menu,
    welcome_menu,
)
from keyboards.admin_panel import back_button, main_menu
from middlewares.admin_auth import require_admin
from models.domain import AdminRole, BroadcastStatus
from services.admin_fsm import (
    STATE_AD_WAIT_ID,
    STATE_BC_WAIT_BUTTONS_JSON,
    STATE_BC_WAIT_MSG,
    STATE_BTN_WAIT_NAME,
    STATE_CH_WAIT_ID,
    STATE_LS_WAIT_MANUAL_URL,
    STATE_LS_WAIT_TEMPLATE,
    STATE_OD_WAIT_BODY,
    STATE_RM_WAIT_BODY,
    STATE_RM_WAIT_HOURS,
    STATE_SCH_WAIT_TIME,
    STATE_WM_BATCH,
    STATE_WM_WAIT,
    AdminFsm,
)
from services.broadcast_service import BroadcastService
from services.outbound_sender import send_from_payload
from services.welcome_flow import send_welcome_sequence
from utils.retention_display import format_retention_delay_human


def _h(x: object) -> str:
    """Escape dynamic fragments for Telegram HTML parse mode."""
    return escape(str(x), quote=False)


async def _owner_only(uid: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    role = await context.application.bot_data["repos"]["admins"].get_role(uid)
    return role == AdminRole.OWNER


def _bc_control_kb(bid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏸ Pause sending", callback_data=f"adm:bc:p:{bid}"),
                InlineKeyboardButton("▶️ Continue", callback_data=f"adm:bc:r:{bid}"),
            ],
            [InlineKeyboardButton("🛑 Stop this send", callback_data=f"adm:bc:c:{bid}")],
            [
                InlineKeyboardButton("📊 See progress", callback_data=f"adm:bc:v:{bid}"),
                InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts"),
            ],
        ]
    )


def _human_broadcast_progress_text(row: dict, st: dict) -> str:
    """Plain-language summary for operators."""
    bid = row.get("id")
    lines = [
        f"📣 **Send #{bid}**",
        "",
        f"**Status:** {row.get('status')}",
        f"**People on your list:** {row.get('total_targets')}",
        f"**Saved counts — Delivered:** {row.get('delivered_count')} · **Problems:** {row.get('failed_count')} · **Blocked you:** {row.get('blocked_count')}",
    ]
    if st:
        def gv(key: str) -> str:
            v = st.get(key)
            return str(v) if v is not None else ""

        lines.extend(
            [
                "",
                "**Right now:**",
                f"· Sent OK: `{gv('delivered') or '0'}`",
                f"· Still waiting: `{gv('remaining') or '?'}`",
                f"· Errors: `{gv('failed') or '0'}` · Blocked: `{gv('blocked') or '0'}`",
                f"· Finished steps: `{gv('processed') or '0'}` / `{gv('total') or '?'}`",
            ]
        )
    return "\n".join(lines)


async def route_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        if update.callback_query:
            await update.callback_query.answer("Denied", show_alert=True)
        return
    q = update.callback_query
    if q is None:
        return
    data = q.data or ""
    await q.answer()

    uid = q.from_user.id if q.from_user else 0
    settings: Settings = context.application.bot_data["settings"]
    redis = context.application.bot_data["redis"]
    users_repo = context.application.bot_data["repos"]["users"]
    settings_repo: SettingsRepository = context.application.bot_data["repos"]["settings"]
    br = context.application.bot_data["repos"]["broadcasts"]
    sr = context.application.bot_data["repos"]["scheduled"]
    admins_repo = context.application.bot_data["repos"]["admins"]
    bc_svc: BroadcastService = context.application.bot_data["services"]["broadcast"]
    fsm: AdminFsm = context.application.bot_data["services"]["fsm"]
    ob = context.application.bot_data["repos"]["onboarding"]

    # --- Navigation ---
    if data == "adm:home":
        await q.edit_message_text("Main menu — tap an option:", reply_markup=main_menu())
        return

    if data == "adm:dashboard":
        stats = await users_repo.get_stats_snapshot()
        text = (
            "📊 <b>Quick summary</b>\n\n"
            f"· Everyone who used the bot: <code>{_h(stats.get('total', 0))}</code>\n"
            f"· Can still receive messages: <code>{_h(stats.get('active', 0))}</code>\n"
            f"· Blocked the bot: <code>{_h(stats.get('blocked', 0))}</code>\n\n"
            "Advanced URL and webhook settings: open <b>Bot setup</b>."
        )
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="HTML")
        return

    if data == "adm:queue":
        qlen = await redis.llen(settings.redis_broadcast_queue)
        active_rows = await br.list_active()
        active_preview = ", ".join(str(int(r["id"])) for r in active_rows[:8]) if active_rows else "—"
        text = (
            "📬 <b>Message sending queue</b>\n\n"
            f"· Lines waiting to send: <code>{_h(qlen)}</code>\n"
            f"· Active send jobs: <code>{_h(active_preview)}</code>\n\n"
            "Clear the line only if support asked you to."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🧹 Clear waiting line", callback_data="adm:queue:clr")],
                [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
            ]
        )
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if data == "adm:queue:clr":
        await redis.delete(settings.redis_broadcast_queue)
        await q.answer("Waiting line cleared.")
        await settings_repo.audit_log("INFO", "queue", "redis cleared", {"admin": uid})
        return

    if data == "adm:health":
        started = float(context.application.bot_data.get("process_started_at") or time.time())
        uptime_s = int(time.time() - started)
        db_ok = False
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            pass
        redis_ok = False
        try:
            await redis.ping()
            redis_ok = True
        except Exception:
            pass
        text = (
            "❤️ <b>Health</b>\n\n"
            f"Uptime: <code>{_h(uptime_s)}</code> s\n"
            f"PostgreSQL: <code>{_h('ok' if db_ok else 'fail')}</code>\n"
            f"Redis: <code>{_h('ok' if redis_ok else 'fail')}</code>\n"
            "Workers: broadcast, scheduler, retention, onboarding (same process)\n"
            f"Onboarding drip env: <code>{_h(settings.onboarding_drip_enabled)}</code>\n"
        )
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="HTML")
        return

    if data == "adm:users":
        stats = await users_repo.get_stats_snapshot()
        win = await users_repo.get_activity_windows()
        ch = await settings_repo.get_channel_settings()
        lines = [f"<code>{_h(k)}</code> → {_h(v)}" for k, v in stats.items()]
        lines.append("")
        lines.append("<b>Activity (last_seen)</b>")
        for k, v in win.items():
            lines.append(f"<code>{_h(k)}</code> → {_h(v)}")
        lines.append("")
        lines.append(f"Join requests (total): {_h(ch.get('join_requests_total', 0))}")
        text = "📈 <b>Users</b>\n\n" + "\n".join(lines)
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="HTML")
        return

    if data == "adm:logs":
        rows = await settings_repo.fetch_recent_logs(12)
        lines = []
        for r in rows:
            snippet = (r["message"] or "")[:80]
            lines.append(
                f"{_h(r['created_at'])} [{_h(r['level'])}] {_h(r['source'])}: {_h(snippet)}"
            )
        body = "\n".join(lines) if lines else "<i>empty</i>"
        text = "📜 <b>Recent logs</b> (DB audit)\n\n" + body
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📎 Full log file", callback_data="adm:logs:doc")],
                [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
            ]
        )
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if data == "adm:logs:doc":
        path = settings.log_file_path
        if not path:
            await q.answer("Set LOG_FILE_PATH in the environment.", show_alert=True)
            return
        if not os.path.isfile(path):
            await q.answer("Log file path not found on disk.", show_alert=True)
            return
        fname = os.path.basename(path)
        with open(path, "rb") as fh:
            await context.bot.send_document(
                chat_id=q.message.chat_id,
                document=InputFile(fh, filename=fname),
                caption="Application log file",
            )
        await q.answer("Sent document.")
        return

    if data == "adm:config":
        ch = await settings_repo.get_channel_settings()
        wc = await users_repo.count_welcome_completions()
        started = float(context.application.bot_data.get("process_started_at") or time.time())
        uptime_s = int(time.time() - started)
        adm_txt = ", ".join(str(x) for x in settings.admin_user_ids[:16])
        pool_ok = False
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            pool_ok = True
        except Exception:
            pass
        redis_ok = False
        try:
            await redis.ping()
            redis_ok = True
        except Exception:
            pass
        text = (
            "⚙️ <b>Configuration</b>\n\n"
            f"Uptime: <code>{_h(uptime_s)}</code> s\n"
            f"/start completions logged: <code>{_h(wc)}</code>\n"
            f"Monitored channel id: <code>{_h(ch.get('monitored_chat_id'))}</code>\n"
            f"Join requests (total): <code>{_h(ch.get('join_requests_total', 0))}</code>\n"
            f"Auto-approve join: <code>{_h(ch.get('auto_approve_join_requests', False))}</code>\n"
            f"Staff ids (env): <code>{_h(adm_txt)}</code>\n"
            f"PostgreSQL: <code>{_h('ok' if pool_ok else 'fail')}</code>\n"
            f"Redis: <code>{_h('ok' if redis_ok else 'fail')}</code>\n"
            f"Onboarding drip: <code>{_h(settings.onboarding_drip_enabled)}</code> "
            f"(env ONBOARDING_DRIP_ENABLED)\n"
            f"Webhook URL: <code>{_h(settings.webhook_full_url())}</code>\n"
        )
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="HTML")
        return

    # --- Broadcasts ---
    if data == "adm:broadcasts":
        n_recipients = await users_repo.count_active_recipients()
        await q.edit_message_text(
            "📣 **Message everyone**\n\n"
            f"Right now **`{n_recipients}`** people can receive your next message "
            "(they pressed Start and did not block the bot).\n\n"
            "Choose an option below.",
            reply_markup=broadcasts_menu(),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:new":
        await fsm.set(uid, {"state": STATE_BC_WAIT_MSG})
        await q.edit_message_text(
            "**Step 1 — Build your message**\n\n"
            "Send **one** message here (text, photo, video, voice note, etc.).\n"
            "If you **forward** a message, the bot copies it.\n\n"
            "/cancel — stop",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:kb":
        await fsm.set(uid, {"state": STATE_BC_WAIT_BUTTONS_JSON})
        await q.edit_message_text(
            "**Extra buttons (optional, advanced)**\n\n"
            "Paste a keyboard in JSON format. Example:\n"
            '`[[{"text":"Join","url":"https://t.me/"}]]`\n\n'
            "/cancel — stop",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:pr":
        draft = await fsm.get_draft_broadcast(uid)
        if not draft:
            await q.answer("Create a message first: tap **Write new message**.", show_alert=True)
            return
        try:
            await send_from_payload(context.bot, chat_id=q.message.chat_id, payload=draft)
            await q.answer("Preview sent here.")
        except Exception as e:
            await q.answer(f"Preview failed: {e}", show_alert=True)
        return

    if data == "adm:bc:sd":
        draft = await fsm.get_draft_broadcast(uid)
        if not draft:
            await q.answer("No message saved yet.", show_alert=True)
            return
        n = await users_repo.count_active_recipients()
        await q.edit_message_text(
            f"**Ready to send?**\n\n"
            f"This goes to about **`{n}`** people.\n\n"
            "Tap **Yes, send now** to start. It may take a few minutes for large lists.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Yes, send now", callback_data="adm:bc:go"),
                        InlineKeyboardButton("❌ Cancel", callback_data="adm:bc:xx"),
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")],
                ]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:go":
        draft = await fsm.get_draft_broadcast(uid)
        if not draft:
            await q.answer("No message saved.", show_alert=True)
            return
        n = await users_repo.count_active_recipients()
        bid = await br.create_broadcast(created_by=uid, payload=draft, status=BroadcastStatus.QUEUED)
        jobs = await bc_svc.enqueue_broadcast(bid)
        await fsm.clear_draft_broadcast(uid)

        if n == 0:
            text = (
                "⚠️ **Nobody received it**\n\n"
                "Your list is empty. Ask users to open the bot and tap **Start**.\n\n"
                "When at least one person has done that, try again."
            )
        elif jobs == 0:
            text = (
                "⚠️ **Could not start**\n\n"
                "Please wait a few seconds and try **Yes, send now** again."
            )
        else:
            text = (
                f"✅ **Sending started**\n\n"
                f"Your message is going to **{n}** people. Large lists take time.\n\n"
                "Tap **Check progress** on the menu to watch results."
            )

        await q.edit_message_text(text, reply_markup=broadcasts_menu(), parse_mode="Markdown")
        await settings_repo.audit_log("INFO", "broadcast", f"queued {bid}", {"admin": uid})
        return

    if data == "adm:bc:xx":
        await fsm.clear_draft_broadcast(uid)
        await fsm.clear(uid)
        await q.edit_message_text("Cancelled.", reply_markup=broadcasts_menu())
        return

    if data == "adm:bc:active":
        rows = await br.list_active()
        if not rows:
            await q.edit_message_text(
                "Nothing is sending right now.", reply_markup=broadcasts_menu(), parse_mode="Markdown"
            )
            return
        kb: list[list[InlineKeyboardButton]] = []
        for r in rows[:12]:
            bid = int(r["id"])
            kb.append(
                [
                    InlineKeyboardButton(
                        f"Send #{bid} ({r['status']})",
                        callback_data=f"adm:bc:v:{bid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")])
        await q.edit_message_text(
            "**Sends in progress** — tap one:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:recent":
        rows = await br.list_recent(12)
        kb = []
        for r in rows:
            bid = int(r["id"])
            kb.append(
                [
                    InlineKeyboardButton(
                        f"Send #{bid} ({r['status']})",
                        callback_data=f"adm:bc:v:{bid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")])
        await q.edit_message_text(
            "**Past sends** — tap for details:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if data.startswith("adm:bc:v:"):
        bid = int(data.split(":")[-1])
        row = await br.get_row(bid)
        if not row:
            await q.answer("Not found.", show_alert=True)
            return
        st_raw = await redis.hgetall(f"broadcast:stats:{bid}")
        st_dict = dict(st_raw) if st_raw else {}
        text = _human_broadcast_progress_text(dict(row), st_dict)
        await q.edit_message_text(text, reply_markup=_bc_control_kb(bid), parse_mode="Markdown")
        return

    if data.startswith("adm:bc:p:"):
        bid = int(data.split(":")[-1])
        await bc_svc.set_paused(bid, True)
        await q.answer("Sending paused.")
        await settings_repo.audit_log("INFO", "broadcast", f"pause {bid}", {"admin": uid})
        return

    if data.startswith("adm:bc:r:"):
        bid = int(data.split(":")[-1])
        await bc_svc.set_paused(bid, False)
        await br.update_status(bid, BroadcastStatus.RUNNING)
        await q.answer("Sending continued.")
        await settings_repo.audit_log("INFO", "broadcast", f"resume {bid}", {"admin": uid})
        return

    if data.startswith("adm:bc:c:"):
        bid = int(data.split(":")[-1])
        await bc_svc.cancel(bid)
        await q.answer("Send stopped.")
        await settings_repo.audit_log("INFO", "broadcast", f"cancel {bid}", {"admin": uid})
        return

    # --- Scheduled ---
    if data == "adm:scheduled":
        await q.edit_message_text("⏱ **Scheduled broadcasts**", reply_markup=scheduled_menu(), parse_mode="Markdown")
        return

    if data == "adm:sch:new":
        await fsm.set(uid, {"state": STATE_SCH_WAIT_TIME})
        await q.edit_message_text(
            "Step 1: send **run time** as ISO UTC, e.g.\n`2026-05-10T15:00:00Z`\n\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:scheduled")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:sch:list":
        jobs = await sr.list_upcoming(20)
        if not jobs:
            await q.edit_message_text(
                "No pending jobs.", reply_markup=scheduled_menu(), parse_mode="Markdown"
            )
            return
        kb = []
        for j in jobs:
            jid = int(j["id"])
            kb.append(
                [
                    InlineKeyboardButton(
                        f"#{jid} @ {j['run_at']}",
                        callback_data=f"adm:sch:x:{jid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:scheduled")])
        await q.edit_message_text(
            "📋 Tap to **cancel** a pending job:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if data.startswith("adm:sch:x:"):
        jid = int(data.split(":")[-1])
        await sr.cancel(jid)
        await q.answer(f"Cancelled job #{jid}.")
        await settings_repo.audit_log("INFO", "scheduler", f"cancel {jid}", {"admin": uid})
        return

    # --- Welcome ---
    if data == "adm:welcome":
        await q.edit_message_text("👋 **Welcome flow**", reply_markup=welcome_menu(), parse_mode="Markdown")
        return

    if data == "adm:wm:add":
        await fsm.set(uid, {"state": STATE_WM_WAIT})
        await q.edit_message_text(
            "Send the welcome step content (supports media).\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:welcome")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:wm:add_batch":
        await fsm.set(uid, {"state": STATE_WM_BATCH, "pending": []})
        await q.edit_message_text(
            "Send welcome steps **one by one** (text or media; forwards use copy mode).\n"
            "When finished, send `/done`.\n\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:welcome")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:wm:pv":
        disp = (q.from_user.first_name if q.from_user else None) or "Friend"
        await send_welcome_sequence(
            context.bot,
            chat_id=q.message.chat_id,
            display_name=disp,
            settings_repo=settings_repo,
        )
        await q.answer("Preview sent to this chat.")
        return

    if data == "adm:onboard":
        rows = await ob.list_messages()
        lines: list[str] = ["🌱 **Onboarding drip** (scheduled from `/start`)\n"]
        for r in rows:
            pl = dict(r.get("payload") or {})
            has_body = bool(pl) and pl != {}
            lines.append(
                f"`{r['step_order']}` — +`{r['delay_seconds']}`s — configured: `{has_body}`"
            )
        lines.append("")
        lines.append("Set `ONBOARDING_DRIP_ENABLED` in the environment to enable sends.")
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=onboarding_menu(),
            parse_mode="Markdown",
        )
        return

    if data.startswith("adm:od:set:"):
        so = int(data.split(":")[-1])
        await fsm.set(uid, {"state": STATE_OD_WAIT_BODY, "od_step": so})
        await q.edit_message_text(
            f"Send onboarding content for step **`{so}`**. Use `{{name}}` in text/captions.\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:onboard")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:wm:list":
        steps = await settings_repo.list_welcome_steps()
        if not steps:
            await q.edit_message_text(
                "No steps.", reply_markup=welcome_menu(), parse_mode="Markdown"
            )
            return
        kb = []
        for s in steps:
            so = int(s["step_order"])
            kb.append(
                [InlineKeyboardButton(f"🗑 Delete step {so}", callback_data=f"adm:wm:d:{so}")]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:welcome")])
        await q.edit_message_text(
            "Welcome steps:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if data.startswith("adm:wm:d:"):
        so = int(data.split(":")[-1])
        await settings_repo.delete_welcome_step(so)
        await q.answer(f"Deleted step {so}.")
        await settings_repo.audit_log("INFO", "welcome", f"delete {so}", {"admin": uid})
        return

    # --- Retention ---
    if data == "adm:retention":
        await q.edit_message_text(
            "♻️ **Come-back messages**\n\n"
            "When someone **leaves** the monitored channel, the bot sends these messages "
            "to their **private chat** with the bot.\n\n"
            "• **Instant step** — send as soon as the bot can (step 1: right after leave; "
            "later steps: right after the previous message).\n"
            "• **Delayed step** — you choose **hours** to wait before that step "
            "(step 1: after they left; step 2+: after the previous come-back message).\n\n"
            "/cancel",
            reply_markup=retention_menu(),
            parse_mode="Markdown",
        )
        return

    if data == "adm:rm:add:i":
        await fsm.set(uid, {"state": STATE_RM_WAIT_BODY, "delay": 0})
        await q.edit_message_text(
            "⚡ **Instant come-back step**\n\n"
            "Send the **message content** now (text, photo, video, etc.). "
            "Forwards are copied.\n\n"
            "`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:retention")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:rm:add:d":
        await fsm.set(uid, {"state": STATE_RM_WAIT_HOURS})
        await q.edit_message_text(
            "⏱ **Delayed come-back step — hours**\n\n"
            "How many **hours** to wait **before** this step?\n\n"
            "• **Step 1:** hours after they **left** the channel.\n"
            "• **Step 2+:** hours after the **previous** come-back message.\n\n"
            "Examples: `1` · `24` · `0.5` (30 minutes)\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:retention")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:rm:list":
        steps = await settings_repo.list_retention_steps()
        if not steps:
            await q.edit_message_text(
                "No come-back steps yet.", reply_markup=retention_menu(), parse_mode="Markdown"
            )
            return
        kb = []
        for s in steps:
            so = int(s["step_order"])
            sec = int(s.get("delay_seconds") or 0)
            label = format_retention_delay_human(sec)
            kb.append(
                [
                    InlineKeyboardButton(
                        f"🗑 Step {so} ({label})",
                        callback_data=f"adm:rm:d:{so}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:retention")])
        await q.edit_message_text(
            "**Come-back steps** (delay before each step):",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if data.startswith("adm:rm:d:"):
        so = int(data.split(":")[-1])
        await settings_repo.delete_retention_step(so)
        await q.answer(f"Deleted step {so}.")
        await settings_repo.audit_log("INFO", "retention", f"delete {so}", {"admin": uid})
        return

    # --- Channel / Livestream ---
    if data == "adm:channel":
        ch = await settings_repo.get_channel_settings()
        ls = await settings_repo.get_livestream_settings()
        murl = str(ls.get("manual_live_url") or "")
        m_preview = (murl[:80] + "…") if len(murl) > 80 else murl
        tmpl = (ls.get("notification_template") or "")[:200]
        text = (
            "📺 <b>Channel &amp; livestream</b>\n\n"
            f"Monitored chat (join requests): <code>{_h(ch.get('monitored_chat_id'))}</code>\n"
            f"Join requests recorded (total): <code>{_h(ch.get('join_requests_total', 0))}</code>\n"
            f"Auto-approve join: <code>{_h(ch.get('auto_approve_join_requests', False))}</code>\n"
            f"Leave-channel retention: <code>{_h(ch.get('retention_enabled'))}</code>\n\n"
            "Manual Join live URL (private channels):\n"
            f"<code>{_h(m_preview or '—')}</code>\n\n"
            "Live template:\n"
            f"<code>{_h(tmpl)}</code>\n"
            f"Cooldown (s): <code>{_h(ls.get('cooldown_seconds'))}</code>"
        )
        await q.edit_message_text(text, reply_markup=channel_live_menu(), parse_mode="HTML")
        return

    if data == "adm:ch:s":
        await fsm.set(uid, {"state": STATE_CH_WAIT_ID})
        await q.edit_message_text(
            "**Forward** any post from the channel here, or send numeric **chat id** "
            "(negative for channels/supergroups).\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:channel")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:ch:auto:1":
        await settings_repo.set_auto_approve_join_requests(True)
        await q.answer("Auto-approve enabled.")
        await settings_repo.audit_log("INFO", "channel", "auto_approve on", {"admin": uid})
        return

    if data == "adm:ch:auto:0":
        await settings_repo.set_auto_approve_join_requests(False)
        await q.answer("Auto-approve disabled.")
        await settings_repo.audit_log("INFO", "channel", "auto_approve off", {"admin": uid})
        return

    if data == "adm:ch:ret:1":
        await settings_repo.set_retention_enabled(True)
        await q.answer("Retention enabled.")
        return

    if data == "adm:ch:ret:0":
        await settings_repo.set_retention_enabled(False)
        await q.answer("Retention disabled.")
        return

    if data == "adm:ls:tpl":
        await fsm.set(uid, {"state": STATE_LS_WAIT_TEMPLATE})
        await q.edit_message_text(
            "Send new **livestream notification text**.\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:channel")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:ls:manual":
        await fsm.set(uid, {"state": STATE_LS_WAIT_MANUAL_URL})
        await q.edit_message_text(
            "**Join live link** — sent with livestream alerts as a **Join now** button.\n\n"
            "Paste an invite or channel URL, e.g.\n"
            "`https://t.me/+AbCd...` or `t.me/+AbCd...`\n\n"
            "`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:channel")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:ls:manual:clr":
        await settings_repo.update_livestream(manual_live_url="")
        await q.answer("Join live link cleared.")
        await settings_repo.audit_log("INFO", "livestream", "manual_live_url cleared", {"admin": uid})
        return

    if data in ("adm:ls:cd:p", "adm:ls:cd:m"):
        ls = await settings_repo.get_livestream_settings()
        cd = int(ls.get("cooldown_seconds") or 300)
        cd = cd + (60 if data.endswith(":p") else -60)
        cd = max(0, cd)
        await settings_repo.update_livestream(cooldown_seconds=cd)
        await q.answer(f"Cooldown = {cd}s")

    # --- Buttons ---
    if data == "adm:buttons":
        await q.edit_message_text("🔘 **Button presets**", reply_markup=buttons_menu(), parse_mode="Markdown")
        return

    if data == "adm:btn:new":
        await fsm.set(uid, {"state": STATE_BTN_WAIT_NAME})
        await q.edit_message_text(
            "Preset **name** (unique).\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:buttons")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:btn:list":
        presets = await settings_repo.list_inline_presets()
        if not presets:
            await q.edit_message_text(
                "No presets.", reply_markup=buttons_menu(), parse_mode="Markdown"
            )
            return
        kb = []
        for p in presets:
            pid = int(p["id"])
            name = (p.get("name") or "")[:24]
            kb.append(
                [
                    InlineKeyboardButton(
                        f"🗑 {name or pid}",
                        callback_data=f"adm:btn:d:{pid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:buttons")])
        await q.edit_message_text("Presets:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if data.startswith("adm:btn:d:"):
        pid = int(data.split(":")[-1])
        await settings_repo.delete_inline_preset(pid)
        await q.answer("Deleted.")
        await settings_repo.audit_log("INFO", "buttons", f"delete {pid}", {"admin": uid})
        return

    # --- Admins ---
    if data == "adm:admins":
        await q.edit_message_text("👮 **Admins**", reply_markup=admins_menu(), parse_mode="Markdown")
        return

    if data == "adm:ad:list":
        rows = await admins_repo.list_admins()
        lines = [f"<code>{_h(r['admin_id'])}</code> — {_h(r['role'])}" for r in rows]
        text = "👮 <b>Admins</b>\n\n" + ("\n".join(lines) if lines else "<i>none</i>")
        kb = []
        if await _owner_only(uid, context):
            for r in rows:
                aid = int(r["admin_id"])
                if aid != uid:
                    kb.append(
                        [InlineKeyboardButton(f"Remove {aid}", callback_data=f"adm:ad:x:{aid}")]
                    )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:admins")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        return

    if data == "adm:ad:new":
        if not await _owner_only(uid, context):
            await q.answer("Owners only.", show_alert=True)
            return
        await fsm.set(uid, {"state": STATE_AD_WAIT_ID})
        await q.edit_message_text(
            "Send Telegram **user id** to grant **support**.\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:admins")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data.startswith("adm:ad:x:"):
        if not await _owner_only(uid, context):
            await q.answer("Owners only.", show_alert=True)
            return
        aid = int(data.split(":")[-1])
        if aid == uid:
            await q.answer("Cannot remove yourself.", show_alert=True)
            return
        owners = await admins_repo.count_owners()
        role = await admins_repo.get_role(aid)
        if role == AdminRole.OWNER and owners <= 1:
            await q.answer("Cannot remove last owner.", show_alert=True)
            return
        await admins_repo.remove_admin(aid)
        await q.answer(f"Removed {aid}.")
        await settings_repo.audit_log("INFO", "admins", f"remove {aid}", {"admin": uid})
        return

    await q.edit_message_text("Unknown action.", reply_markup=main_menu())
