"""Build PTB InlineKeyboardMarkup from JSON rows (admin builder storage)."""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def markup_from_json(rows: list[list[dict[str, Any]]] | None) -> InlineKeyboardMarkup | None:
    """Convert stored JSON button definitions to InlineKeyboardMarkup."""
    if not rows:
        return None
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in rows:
        btn_row: list[InlineKeyboardButton] = []
        for b in row:
            text = str(b.get("text") or "")
            if "url" in b and b["url"]:
                btn_row.append(InlineKeyboardButton(text=text, url=str(b["url"])))
            elif "web_app" in b and isinstance(b["web_app"], dict):
                from telegram import WebAppInfo

                wa = WebAppInfo(url=str(b["web_app"].get("url", "")))
                btn_row.append(InlineKeyboardButton(text=text, web_app=wa))
            elif "callback_data" in b and b["callback_data"] is not None:
                btn_row.append(
                    InlineKeyboardButton(text=text, callback_data=str(b["callback_data"])[:64])
                )
            elif "switch_inline_query" in b:
                btn_row.append(
                    InlineKeyboardButton(
                        text=text,
                        switch_inline_query=str(b.get("switch_inline_query") or ""),
                    )
                )
            elif "switch_inline_query_current_chat" in b:
                btn_row.append(
                    InlineKeyboardButton(
                        text=text,
                        switch_inline_query_current_chat=str(
                            b.get("switch_inline_query_current_chat") or ""
                        ),
                    )
                )
            else:
                # fallback minimal callback
                btn_row.append(InlineKeyboardButton(text=text or "—", callback_data="noop"))
        if btn_row:
            keyboard.append(btn_row)
    return InlineKeyboardMarkup(keyboard) if keyboard else None
