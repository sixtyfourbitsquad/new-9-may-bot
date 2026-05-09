"""Dynamic inline keyboards for admin panel sections."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def broadcasts_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ New broadcast", callback_data="adm:bc:new")],
            [InlineKeyboardButton("▶️ Active / control", callback_data="adm:bc:active")],
            [InlineKeyboardButton("📜 Recent", callback_data="adm:bc:recent")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
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
            [InlineKeyboardButton("📋 List steps", callback_data="adm:wm:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
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
            [InlineKeyboardButton("🎯 Set monitored chat id", callback_data="adm:ch:s")],
            [
                InlineKeyboardButton("Retention ON", callback_data="adm:ch:ret:1"),
                InlineKeyboardButton("Retention OFF", callback_data="adm:ch:ret:0"),
            ],
            [InlineKeyboardButton("📡 Livestream text", callback_data="adm:ls:tpl")],
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
