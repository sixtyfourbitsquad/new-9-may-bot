"""Dynamic inline keyboards for admin panel sections."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def broadcasts_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Write new message", callback_data="adm:bc:new")],
            [InlineKeyboardButton("📊 Check progress", callback_data="adm:bc:active")],
            [InlineKeyboardButton("📜 Past sends", callback_data="adm:bc:recent")],
            [InlineKeyboardButton("⬅️ Main menu", callback_data="adm:home")],
        ]
    )


def scheduled_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Schedule broadcast", callback_data="adm:sch:new")],
            [InlineKeyboardButton("📋 Upcoming jobs", callback_data="adm:sch:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
        ]
    )


def welcome_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add step", callback_data="adm:wm:add")],
            [InlineKeyboardButton("📝 Batch add (/done)", callback_data="adm:wm:add_batch")],
            [InlineKeyboardButton("👁 Preview sequence", callback_data="adm:wm:pv")],
            [InlineKeyboardButton("📋 List steps", callback_data="adm:wm:list")],
            [InlineKeyboardButton("🌱 Onboarding drip", callback_data="adm:onboard")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
        ]
    )


def onboarding_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Set step 1 (+1h)", callback_data="adm:od:set:1")],
            [InlineKeyboardButton("Set step 2 (+1d)", callback_data="adm:od:set:2")],
            [InlineKeyboardButton("Set step 3 (+3d)", callback_data="adm:od:set:3")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:welcome")],
        ]
    )


def retention_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add step", callback_data="adm:rm:add")],
            [InlineKeyboardButton("📋 List steps", callback_data="adm:rm:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
        ]
    )


def channel_live_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎯 Set channel (forward or id)", callback_data="adm:ch:s")],
            [
                InlineKeyboardButton("✅ Auto-approve ON", callback_data="adm:ch:auto:1"),
                InlineKeyboardButton("⛔ Auto-approve OFF", callback_data="adm:ch:auto:0"),
            ],
            [
                InlineKeyboardButton("Retention ON", callback_data="adm:ch:ret:1"),
                InlineKeyboardButton("Retention OFF", callback_data="adm:ch:ret:0"),
            ],
            [InlineKeyboardButton("📡 Livestream text", callback_data="adm:ls:tpl")],
            [
                InlineKeyboardButton("🔗 Join live link (private)", callback_data="adm:ls:manual"),
                InlineKeyboardButton("🗑 Clear link", callback_data="adm:ls:manual:clr"),
            ],
            [
                InlineKeyboardButton("Cooldown −60s", callback_data="adm:ls:cd:m"),
                InlineKeyboardButton("Cooldown +60s", callback_data="adm:ls:cd:p"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
        ]
    )


def buttons_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ New preset", callback_data="adm:btn:new")],
            [InlineKeyboardButton("📋 List presets", callback_data="adm:btn:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
        ]
    )


def admins_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 List admins", callback_data="adm:ad:list")],
            [InlineKeyboardButton("➕ Add support (owner)", callback_data="adm:ad:new")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
        ]
    )
