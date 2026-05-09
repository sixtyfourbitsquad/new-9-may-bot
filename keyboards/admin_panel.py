"""Inline admin panel menus (callback_data prefixes: `adm:`)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="adm:dashboard"),
                InlineKeyboardButton("📣 Broadcasts", callback_data="adm:broadcasts"),
            ],
            [
                InlineKeyboardButton("⏱ Scheduled", callback_data="adm:scheduled"),
                InlineKeyboardButton("👋 Welcome", callback_data="adm:welcome"),
            ],
            [
                InlineKeyboardButton("♻️ Retention", callback_data="adm:retention"),
                InlineKeyboardButton("📺 Channel / Live", callback_data="adm:channel"),
            ],
            [
                InlineKeyboardButton("📈 Users", callback_data="adm:users"),
                InlineKeyboardButton("📜 Logs", callback_data="adm:logs"),
            ],
            [
                InlineKeyboardButton("🧱 Queue", callback_data="adm:queue"),
                InlineKeyboardButton("👮 Admins", callback_data="adm:admins"),
            ],
            [
                InlineKeyboardButton("🔘 Buttons", callback_data="adm:buttons"),
                InlineKeyboardButton("❤️ Health", callback_data="adm:health"),
            ],
        ]
    )


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data="adm:home")]]
    )
