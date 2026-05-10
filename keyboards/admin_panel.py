"""Inline admin panel menus (callback_data prefixes: `adm:`)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Summary", callback_data="adm:dashboard"),
                InlineKeyboardButton("📣 Message everyone", callback_data="adm:broadcasts"),
            ],
            [
                InlineKeyboardButton("⏱ Send later", callback_data="adm:scheduled"),
                InlineKeyboardButton("👋 New user welcome", callback_data="adm:welcome"),
            ],
            [
                InlineKeyboardButton("♻️ Come-back messages", callback_data="adm:retention"),
                InlineKeyboardButton("📺 Channel", callback_data="adm:channel"),
            ],
            [
                InlineKeyboardButton("📈 People stats", callback_data="adm:users"),
                InlineKeyboardButton("📜 Logs", callback_data="adm:logs"),
            ],
            [
                InlineKeyboardButton("📬 Send queue", callback_data="adm:queue"),
                InlineKeyboardButton("❤️ Server check", callback_data="adm:health"),
            ],
            [
                InlineKeyboardButton("⚙️ Bot setup", callback_data="adm:config"),
            ],
        ]
    )


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data="adm:home")]]
    )
