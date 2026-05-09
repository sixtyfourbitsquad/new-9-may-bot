"""Inline admin panel — full CRUD / controls."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from configs.settings import Settings
from database.repositories.settings_repo import SettingsRepository
from keyboards.admin_menus import (
    admins_menu,
    broadcasts_menu,
    buttons_menu,
    channel_live_menu,
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
    STATE_LS_WAIT_TEMPLATE,
    STATE_RM_WAIT_DELAY,
    STATE_SCH_WAIT_TIME,
    STATE_WM_WAIT,
    AdminFsm,
)
from services.broadcast_service import BroadcastService
from services.outbound_sender import send_from_payload


async def _owner_only(uid: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    role = await context.application.bot_data["repos"]["admins"].get_role(uid)
    return role == AdminRole.OWNER


def _bc_control_kb(bid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏸ Pause", callback_data=f"adm:bc:p:{bid}"),
                InlineKeyboardButton("▶️ Resume", callback_data=f"adm:bc:r:{bid}"),
            ],
            [InlineKeyboardButton("🛑 Cancel job", callback_data=f"adm:bc:c:{bid}")],
            [
                InlineKeyboardButton("📊 Stats", callback_data=f"adm:bc:v:{bid}"),
                InlineKeyboardButton("⬅️ Broadcasts", callback_data="adm:broadcasts"),
            ],
        ]
    )


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

    # --- Navigation ---
    if data == "adm:home":
        await q.edit_message_text("🔧 Admin panel", reply_markup=main_menu())
        return

    if data == "adm:dashboard":
        stats = await users_repo.get_stats_snapshot()
        text = (
            "📊 **Dashboard**\n\n"
            f"Users: `{stats.get('total', 0)}` | active: `{stats.get('active', 0)}` | blocked: `{stats.get('blocked', 0)}`\n"
            f"Webhook: `{settings.webhook_full_url()}`"
        )
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="Markdown")
        return

    if data == "adm:queue":
        qlen = await redis.llen(settings.redis_broadcast_queue)
        text = f"🧱 **Queue**\n\nBroadcast Redis depth: `{qlen}`"
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="Markdown")
        return

    if data == "adm:health":
        await q.edit_message_text(
            "❤️ **Health**\n\nPostgreSQL pool + Redis + workers run inside this process.",
            reply_markup=back_button(),
            parse_mode="Markdown",
        )
        return

    if data == "adm:users":
        stats = await users_repo.get_stats_snapshot()
        text = "📈 **Users**\n\n" + "\n".join(f"`{k}` → `{v}`" for k, v in stats.items())
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="Markdown")
        return

    if data == "adm:logs":
        rows = await settings_repo.fetch_recent_logs(12)
        lines = []
        for r in rows:
            lines.append(
                f"`{r['created_at']}` [{r['level']}] {r['source']}: {(r['message'] or '')[:80]}"
            )
        text = "📜 **Recent logs**\n\n" + ("\n".join(lines) if lines else "_empty_")
        await q.edit_message_text(text, reply_markup=back_button(), parse_mode="Markdown")
        return

    # --- Broadcasts ---
    if data == "adm:broadcasts":
        await q.edit_message_text("📣 **Broadcast manager**", reply_markup=broadcasts_menu(), parse_mode="Markdown")
        return

    if data == "adm:bc:new":
        await fsm.set(uid, {"state": STATE_BC_WAIT_MSG})
        await q.edit_message_text(
            "Send me **one message** in private chat (text, photo, video, poll, sticker…).\n"
            "Forwarded messages use copy mode.\n\n`/cancel` to abort.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:kb":
        await fsm.set(uid, {"state": STATE_BC_WAIT_BUTTONS_JSON})
        await q.edit_message_text(
            "Paste **inline keyboard JSON** (array of rows). Example:\n"
            '`[[{"text":"Join","url":"https://t.me/"}]]`\n\n`/cancel`',
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")]]
            ),
            parse_mode="Markdown",
        )
        return

    if data == "adm:bc:pr":
        draft = await fsm.get_draft_broadcast(uid)
        if not draft:
            await q.answer("No draft — create New broadcast first.", show_alert=True)
            return
        try:
            await send_from_payload(context.bot, chat_id=q.message.chat_id, payload=draft)
            await q.answer("Preview sent.")
        except Exception as e:
            await q.answer(f"Preview failed: {e}", show_alert=True)
        return

    if data == "adm:bc:sd":
        draft = await fsm.get_draft_broadcast(uid)
        if not draft:
            await q.answer("No draft.", show_alert=True)
            return
        bid = await br.create_broadcast(created_by=uid, payload=draft, status=BroadcastStatus.QUEUED)
        await bc_svc.enqueue_broadcast(bid)
        await fsm.clear_draft_broadcast(uid)
        await q.edit_message_text(
            f"🚀 Broadcast **`#{bid}`** queued.\n\nTrack in Active / Recent.",
            reply_markup=broadcasts_menu(),
            parse_mode="Markdown",
        )
        await settings_repo.audit_log("INFO", "broadcast", f"queued {bid}", {"admin": uid})
        return

    if data == "adm:bc:xx":
        await fsm.clear_draft_broadcast(uid)
        await fsm.clear(uid)
        await q.edit_message_text("Draft discarded.", reply_markup=broadcasts_menu())
        return

    if data == "adm:bc:active":
        rows = await br.list_active()
        if not rows:
            await q.edit_message_text(
                "No active broadcasts.", reply_markup=broadcasts_menu(), parse_mode="Markdown"
            )
            return
        kb: list[list[InlineKeyboardButton]] = []
        for r in rows[:12]:
            bid = int(r["id"])
            kb.append(
                [
                    InlineKeyboardButton(
                        f"#{bid} {r['status']}",
                        callback_data=f"adm:bc:v:{bid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")])
        await q.edit_message_text(
            "▶️ **Active broadcasts** — tap for controls:",
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
                        f"#{bid} {r['status']}",
                        callback_data=f"adm:bc:v:{bid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:broadcasts")])
        await q.edit_message_text(
            "📜 **Recent broadcasts**",
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
        st = await redis.hgetall(f"broadcast:stats:{bid}")
        text = (
            f"📊 **Broadcast `{bid}`**\n"
            f"Status: `{row.get('status')}`\n"
            f"Targets: `{row.get('total_targets')}`\n"
            f"DB delivered/failed/blocked: `{row.get('delivered_count')}` / `{row.get('failed_count')}` / `{row.get('blocked_count')}`\n"
        )
        if st:
            text += "\n**Redis live:**\n" + "\n".join(f"`{k}` → `{v}`" for k, v in st.items())
        await q.edit_message_text(text, reply_markup=_bc_control_kb(bid), parse_mode="Markdown")
        return

    if data.startswith("adm:bc:p:"):
        bid = int(data.split(":")[-1])
        await bc_svc.set_paused(bid, True)
        await q.answer("Paused.")
        await settings_repo.audit_log("INFO", "broadcast", f"pause {bid}", {"admin": uid})
        return

    if data.startswith("adm:bc:r:"):
        bid = int(data.split(":")[-1])
        await bc_svc.set_paused(bid, False)
        await br.update_status(bid, BroadcastStatus.RUNNING)
        await q.answer("Resumed.")
        await settings_repo.audit_log("INFO", "broadcast", f"resume {bid}", {"admin": uid})
        return

    if data.startswith("adm:bc:c:"):
        bid = int(data.split(":")[-1])
        await bc_svc.cancel(bid)
        await q.answer("Cancelled.")
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
        await q.edit_message_text("♻️ **Retention**", reply_markup=retention_menu(), parse_mode="Markdown")
        return

    if data == "adm:rm:add":
        await fsm.set(uid, {"state": STATE_RM_WAIT_DELAY})
        await q.edit_message_text(
            "First send **delay seconds** before this message (e.g. `3600`).\n`/cancel`",
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
                "No retention steps.", reply_markup=retention_menu(), parse_mode="Markdown"
            )
            return
        kb = []
        for s in steps:
            so = int(s["step_order"])
            kb.append([InlineKeyboardButton(f"🗑 Delete step {so}", callback_data=f"adm:rm:d:{so}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:retention")])
        await q.edit_message_text(
            "Retention steps:",
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
        text = (
            "📺 **Channel & livestream**\n\n"
            f"Monitored chat: `{ch.get('monitored_chat_id')}`\n"
            f"Retention: `{ch.get('retention_enabled')}`\n\n"
            f"Live template:\n`{(ls.get('notification_template') or '')[:200]}`\n"
            f"Cooldown (s): `{ls.get('cooldown_seconds')}`"
        )
        await q.edit_message_text(text, reply_markup=channel_live_menu(), parse_mode="Markdown")
        return

    if data == "adm:ch:s":
        await fsm.set(uid, {"state": STATE_CH_WAIT_ID})
        await q.edit_message_text(
            "Send numeric **chat id** (negative for channels/groups).\n`/cancel`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="adm:channel")]]
            ),
            parse_mode="Markdown",
        )
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
        lines = [f"`{r['admin_id']}` — `{r['role']}`" for r in rows]
        text = "👮 **Admins**\n\n" + ("\n".join(lines) if lines else "_none_")
        kb = []
        if await _owner_only(uid, context):
            for r in rows:
                aid = int(r["admin_id"])
                if aid != uid:
                    kb.append(
                        [InlineKeyboardButton(f"Remove {aid}", callback_data=f"adm:ad:x:{aid}")]
                    )
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:admins")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
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
