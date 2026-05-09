"""Forward user traffic to admin inbox and route admin replies back."""

from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import Redis
from telegram import Bot, Message
from telegram.constants import ChatType
from telegram.error import Forbidden, TelegramError

from database.repositories.users import UserRepository
from models.domain import UserBroadcastStatus
from services.rate_limit_service import RateLimitService
from utils.flood import with_flood_wait

logger = logging.getLogger(__name__)


class LiveChatService:
    """
    Forwards non-command user messages to admin_chat_id.

    Admin replies to forwarded messages are routed using forward_origin when possible.
    Fallback: Redis mapping admin_chat_message_id -> target_user_id for copy_message flows.
    """

    def __init__(
        self,
        *,
        admin_chat_id: int,
        users: UserRepository,
        redis: Redis,
        redis_mapping_prefix: str = "lcmap:",
        rate: RateLimitService,
        user_rate_per_minute: int = 30,
        admin_rate_per_minute: int = 120,
    ) -> None:
        self._admin_chat_id = admin_chat_id
        self._users = users
        self._redis = redis
        self._prefix = redis_mapping_prefix
        self._rate = rate
        self._user_rpm = user_rate_per_minute
        self._admin_rpm = admin_rate_per_minute

    async def forward_user_message(self, bot: Bot, message: Message) -> None:
        """Forward or copy user message into admin inbox."""
        if message.chat.type != ChatType.PRIVATE:
            return
        uid = message.from_user.id if message.from_user else None
        if uid is None:
            return
        ok = await self._rate.allow(f"user:{uid}", limit=self._user_rpm, window_seconds=60)
        if not ok:
            logger.info("Rate limited user %s", uid)
            return
        try:
            fwd = await with_flood_wait(
                lambda: bot.forward_message(
                    chat_id=self._admin_chat_id,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
            )
            if fwd and getattr(message, "media_group_id", None):
                key = f"{self._prefix}{fwd.message_id}"
                await self._redis.set(key, str(uid), ex=86400 * 7)
        except (TelegramError, Forbidden) as e:
            logger.warning("Forward failed, falling back to copy/text: %s", e)
            try:
                copied = await with_flood_wait(
                    lambda: bot.copy_message(
                        chat_id=self._admin_chat_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                )
                key = f"{self._prefix}{copied.message_id}"
                await self._redis.set(key, str(uid), ex=86400 * 7)
            except TelegramError:
                text = (message.text or message.caption or "")[:3500]
                await with_flood_wait(
                    lambda: bot.send_message(
                        chat_id=self._admin_chat_id,
                        text=f"[fallback] user `{uid}`:\n{text}",
                        parse_mode="Markdown",
                    )
                )

    async def relay_admin_reply(self, bot: Bot, message: Message) -> bool:
        """
        If message is a reply in admin chat, deliver payload back to end user.

        Returns True if handled.
        """
        if message.chat_id != self._admin_chat_id:
            return False
        if message.reply_to_message is None:
            return False
        target_user_id: Optional[int] = None
        rmsg = message.reply_to_message
        if rmsg.forward_origin:
            fo = rmsg.forward_origin
            sender_user = getattr(fo, "sender_user", None)
            if sender_user is not None:
                target_user_id = int(sender_user.id)
        if target_user_id is None:
            mid = rmsg.message_id
            key = f"{self._prefix}{mid}"
            raw = await self._redis.get(key)
            if raw:
                target_user_id = int(raw)
        if target_user_id is None:
            return False

        aid = message.from_user.id if message.from_user else 0
        ok = await self._rate.allow(f"admin:{aid}", limit=self._admin_rpm, window_seconds=60)
        if not ok:
            return True

        try:
            if message.text:
                await with_flood_wait(
                    lambda: bot.send_message(chat_id=target_user_id, text=message.text)
                )
            elif message.photo:
                p = message.photo[-1]
                await with_flood_wait(
                    lambda: bot.send_photo(
                        chat_id=target_user_id,
                        photo=p.file_id,
                        caption=message.caption,
                    )
                )
            elif message.video:
                await with_flood_wait(
                    lambda: bot.send_video(
                        chat_id=target_user_id,
                        video=message.video.file_id,
                        caption=message.caption,
                    )
                )
            elif message.voice:
                await with_flood_wait(
                    lambda: bot.send_voice(
                        chat_id=target_user_id,
                        voice=message.voice.file_id,
                        caption=message.caption,
                    )
                )
            elif message.audio:
                await with_flood_wait(
                    lambda: bot.send_audio(
                        chat_id=target_user_id,
                        audio=message.audio.file_id,
                        caption=message.caption,
                    )
                )
            elif message.document:
                await with_flood_wait(
                    lambda: bot.send_document(
                        chat_id=target_user_id,
                        document=message.document.file_id,
                        caption=message.caption,
                    )
                )
            elif message.animation:
                await with_flood_wait(
                    lambda: bot.send_animation(
                        chat_id=target_user_id,
                        animation=message.animation.file_id,
                        caption=message.caption,
                    )
                )
            elif message.sticker:
                await with_flood_wait(
                    lambda: bot.send_sticker(
                        chat_id=target_user_id,
                        sticker=message.sticker.file_id,
                    )
                )
            else:
                await with_flood_wait(
                    lambda: bot.copy_message(
                        chat_id=target_user_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                )
        except Forbidden:
            await self._users.set_broadcast_status(target_user_id, UserBroadcastStatus.BLOCKED)
        except TelegramError as e:
            logger.error("Relay admin reply failed: %s", e)
        return True
