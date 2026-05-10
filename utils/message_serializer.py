"""Serialize Telegram Message → outbound payload dict for DB/broadcast."""

from __future__ import annotations

from typing import Any

from telegram import Message


def _inline_keyboard_rows_from_message(message: Message) -> list[list[dict[str, Any]]] | None:
    """When the admin message includes inline buttons, store them for outbound_sender."""
    rm = message.reply_markup
    if rm is None:
        return None
    ikb = getattr(rm, "inline_keyboard", None)
    if not ikb:
        return None
    rows: list[list[dict[str, Any]]] = []
    for row in ikb:
        out_row: list[dict[str, Any]] = []
        for btn in row:
            d: dict[str, Any] = {"text": getattr(btn, "text", None) or "—"}
            if getattr(btn, "url", None):
                d["url"] = str(btn.url)
            elif getattr(btn, "callback_data", None) is not None:
                d["callback_data"] = str(btn.callback_data)
            elif getattr(btn, "web_app", None) is not None:
                wa = btn.web_app
                d["web_app"] = {"url": str(getattr(wa, "url", "") or "")}
            elif getattr(btn, "switch_inline_query", None) is not None:
                d["switch_inline_query"] = str(btn.switch_inline_query or "")
            elif getattr(btn, "switch_inline_query_current_chat", None) is not None:
                d["switch_inline_query_current_chat"] = str(btn.switch_inline_query_current_chat or "")
            else:
                continue
            out_row.append(d)
        if out_row:
            rows.append(out_row)
    return rows or None


def _attach_inline_keyboard(payload: dict[str, Any], message: Message) -> dict[str, Any]:
    rows = _inline_keyboard_rows_from_message(message)
    if not rows:
        return payload
    out = dict(payload)
    out["inline_keyboard"] = rows
    return out


def message_to_payload(message: Message) -> dict[str, Any]:
    """Map a received Message to `services/outbound_sender` payload shape."""
    # Forwarded / copyMessage preserves formatting reliably
    if message.forward_origin is not None:
        return _attach_inline_keyboard(
            {
                "kind": "copy",
                "from_chat_id": message.chat_id,
                "message_id": message.message_id,
            },
            message,
        )

    if message.poll:
        opts = [o.text for o in message.poll.options]
        return _attach_inline_keyboard(
            {
                "kind": "poll",
                "question": message.poll.question,
                "options": opts,
                "is_anonymous": bool(getattr(message.poll, "is_anonymous", True)),
                "allows_multiple_answers": bool(getattr(message.poll, "allows_multiple_answers", False)),
            },
            message,
        )

    if message.text:
        return _attach_inline_keyboard({"kind": "text", "text": message.text}, message)

    if message.photo:
        return _attach_inline_keyboard(
            {
                "kind": "photo",
                "file_id": message.photo[-1].file_id,
                "caption": message.caption,
            },
            message,
        )

    if message.video:
        return _attach_inline_keyboard(
            {
                "kind": "video",
                "file_id": message.video.file_id,
                "caption": message.caption,
            },
            message,
        )

    if message.animation:
        return _attach_inline_keyboard(
            {
                "kind": "animation",
                "file_id": message.animation.file_id,
                "caption": message.caption,
            },
            message,
        )

    if message.voice:
        return _attach_inline_keyboard(
            {"kind": "voice", "file_id": message.voice.file_id, "caption": message.caption},
            message,
        )

    if message.audio:
        return _attach_inline_keyboard(
            {"kind": "audio", "file_id": message.audio.file_id, "caption": message.caption},
            message,
        )

    if message.document:
        return _attach_inline_keyboard(
            {"kind": "document", "file_id": message.document.file_id, "caption": message.caption},
            message,
        )

    if message.sticker:
        return _attach_inline_keyboard({"kind": "sticker", "file_id": message.sticker.file_id}, message)

    if message.video_note:
        return _attach_inline_keyboard(
            {"kind": "video_note", "file_id": message.video_note.file_id},
            message,
        )

    return _attach_inline_keyboard(
        {
            "kind": "copy",
            "from_chat_id": message.chat_id,
            "message_id": message.message_id,
        },
        message,
    )
