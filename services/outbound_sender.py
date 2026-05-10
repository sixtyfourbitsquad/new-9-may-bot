"""Send outbound messages from stored JSON payloads (broadcast / scheduler / retention)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from telegram import Bot, InputMediaAnimation, InputMediaAudio, InputMediaDocument
from telegram import InputMediaPhoto, InputMediaVideo, Message
from utils.flood import with_flood_wait
from utils.keyboard_json import markup_from_json

logger = logging.getLogger(__name__)


async def send_from_payload(
    bot: Bot,
    *,
    chat_id: int,
    payload: dict[str, Any],
) -> Message:
    """
    Dispatch a stored payload to chat_id.

    Expected keys:
      kind: text|photo|video|voice|audio|document|sticker|animation|poll|album|forward|copy|video_note
      ... kind-specific fields
      inline_keyboard: optional rows
    """
    kind = str(payload.get("kind") or "text")
    markup = markup_from_json(payload.get("inline_keyboard"))
    disable_notification = bool(payload.get("disable_notification", False))

    if kind == "text":
        text = str(payload.get("text") or "")
        parse_mode = payload.get("parse_mode")
        return await with_flood_wait(
            lambda: bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "photo":
        return await with_flood_wait(
            lambda: bot.send_photo(
                chat_id=chat_id,
                photo=str(payload.get("file_id") or payload.get("photo")),
                caption=payload.get("caption"),
                parse_mode=payload.get("parse_mode"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "video":
        return await with_flood_wait(
            lambda: bot.send_video(
                chat_id=chat_id,
                video=str(payload.get("file_id") or payload.get("video")),
                caption=payload.get("caption"),
                parse_mode=payload.get("parse_mode"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "voice":
        return await with_flood_wait(
            lambda: bot.send_voice(
                chat_id=chat_id,
                voice=str(payload.get("file_id") or payload.get("voice")),
                caption=payload.get("caption"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "video_note":
        return await with_flood_wait(
            lambda: bot.send_video_note(
                chat_id=chat_id,
                video_note=str(payload.get("file_id") or payload.get("video_note")),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "audio":
        return await with_flood_wait(
            lambda: bot.send_audio(
                chat_id=chat_id,
                audio=str(payload.get("file_id") or payload.get("audio")),
                caption=payload.get("caption"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "document":
        return await with_flood_wait(
            lambda: bot.send_document(
                chat_id=chat_id,
                document=str(payload.get("file_id") or payload.get("document")),
                caption=payload.get("caption"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "sticker":
        return await with_flood_wait(
            lambda: bot.send_sticker(
                chat_id=chat_id,
                sticker=str(payload.get("file_id") or payload.get("sticker")),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "animation":
        return await with_flood_wait(
            lambda: bot.send_animation(
                chat_id=chat_id,
                animation=str(payload.get("file_id") or payload.get("animation")),
                caption=payload.get("caption"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "poll":
        return await with_flood_wait(
            lambda: bot.send_poll(
                chat_id=chat_id,
                question=str(payload.get("question") or ""),
                options=list(payload.get("options") or []),
                is_anonymous=bool(payload.get("is_anonymous", True)),
                allows_multiple_answers=bool(payload.get("allows_multiple_answers", False)),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    if kind == "album":
        items = list(payload.get("items") or [])
        media: list[Any] = []
        for it in items:
            t = str(it.get("type") or "photo")
            if t == "photo":
                media.append(InputMediaPhoto(media=str(it.get("media")), caption=it.get("caption")))
            elif t == "video":
                media.append(InputMediaVideo(media=str(it.get("media")), caption=it.get("caption")))
            elif t == "document":
                media.append(InputMediaDocument(media=str(it.get("media")), caption=it.get("caption")))
            elif t == "audio":
                media.append(InputMediaAudio(media=str(it.get("media")), caption=it.get("caption")))
            elif t == "animation":
                media.append(InputMediaAnimation(media=str(it.get("media")), caption=it.get("caption")))
        msgs = await with_flood_wait(lambda: bot.send_media_group(chat_id=chat_id, media=media))
        return msgs[-1]

    if kind == "forward":
        return await with_flood_wait(
            lambda: bot.forward_message(
                chat_id=chat_id,
                from_chat_id=int(payload["from_chat_id"]),
                message_id=int(payload["message_id"]),
                disable_notification=disable_notification,
            )
        )

    if kind == "copy":
        return await with_flood_wait(
            lambda: bot.copy_message(
                chat_id=chat_id,
                from_chat_id=int(payload["from_chat_id"]),
                message_id=int(payload["message_id"]),
                caption=payload.get("caption"),
                reply_markup=markup,
                disable_notification=disable_notification,
            )
        )

    raise ValueError(f"Unsupported payload kind: {kind}")


def merge_inline_keyboard(payload: dict[str, Any], extra_rows: Optional[list] = None) -> dict[str, Any]:
    """Merge admin preset buttons into payload copy."""
    p = dict(payload)
    existing = list(p.get("inline_keyboard") or [])
    if extra_rows:
        existing.extend(extra_rows)
    if existing:
        p["inline_keyboard"] = existing
    return p
