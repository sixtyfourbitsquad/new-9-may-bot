"""Serialize Telegram Message → outbound payload dict for DB/broadcast."""

from __future__ import annotations

from typing import Any

from telegram import Message


def message_to_payload(message: Message) -> dict[str, Any]:
    """Map a received Message to `services.outbound_sender` payload shape."""
    # Forwarded / copyMessage preserves formatting reliably
    if message.forward_origin is not None:
        return {
            "kind": "copy",
            "from_chat_id": message.chat_id,
            "message_id": message.message_id,
        }

    if message.poll:
        opts = [o.text for o in message.poll.options]
        return {
            "kind": "poll",
            "question": message.poll.question,
            "options": opts,
            "is_anonymous": bool(getattr(message.poll, "is_anonymous", True)),
            "allows_multiple_answers": bool(getattr(message.poll, "allows_multiple_answers", False)),
        }

    if message.text:
        return {"kind": "text", "text": message.text}

    if message.photo:
        return {
            "kind": "photo",
            "file_id": message.photo[-1].file_id,
            "caption": message.caption,
        }

    if message.video:
        return {
            "kind": "video",
            "file_id": message.video.file_id,
            "caption": message.caption,
        }

    if message.animation:
        return {
            "kind": "animation",
            "file_id": message.animation.file_id,
            "caption": message.caption,
        }

    if message.voice:
        return {"kind": "voice", "file_id": message.voice.file_id, "caption": message.caption}

    if message.audio:
        return {"kind": "audio", "file_id": message.audio.file_id, "caption": message.caption}

    if message.document:
        return {"kind": "document", "file_id": message.document.file_id, "caption": message.caption}

    if message.sticker:
        return {"kind": "sticker", "file_id": message.sticker.file_id}

    if message.video_note:
        return {"kind": "video_note", "file_id": message.video_note.file_id}

    return {
        "kind": "copy",
        "from_chat_id": message.chat_id,
        "message_id": message.message_id,
    }
