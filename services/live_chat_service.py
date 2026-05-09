"""Forward user traffic to admins' private chats and route replies back."""

from __future__ import annotations

import logging
from typing import Collection, FrozenSet, Optional

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
    Forwards non-command user messages to each configured admin user id (private inbox).

    Admin replies (in private chat with the bot) are routed using forward_origin or
    Redis fallback keyed per admin inbox message.
    """

    def __init__(
        self,
        *,
        admin_user_ids: Collection[int],
        users: UserRepository,
        redis: Redis,
        redis_mapping_prefix: str = "lcmap:",
        rate: RateLimitService,
        user_rate_per_minute: int = 30,
        admin_rate_per_minute: int = 120,
    ) -> None:
        self._admin_ids: FrozenSet[int] = frozenset(int(x) for x in admin_user_ids)
        if not self._admin_ids:
            raise ValueError("admin_user_ids must not be empty")
        self._users = users
        self._redis = redis
        self._prefix = redis_mapping_prefix
        self._rate = rate
        self._user_rpm = user_rate_per_minute
        self._admin_rpm = admin_rate_per_minute

    def admin_ids(self) -> FrozenSet[int]:
        return self._admin_ids

    def _map_key(self, inbox_chat_id: int, inbox_message_id: int) -> str:
        """Redis key for copy_message fallback (unique per admin inbox)."""
        return f"{self._prefix}{inbox_chat_id}:{inbox_message_id}"

    async def forward_user_message(self, bot: Bot, message: Message) -> None:
        """Forward or copy user message to every admin private inbox."""
        if message.chat.type != ChatType.PRIVATE:
            return
        uid = message.from_user.id if message.from_user else None
        if uid is None:
            return
        if uid in self._admin_ids:
            return
        ok = await self._rate.allow(f"user:{uid}", limit=self._user_rpm, window_seconds=60)
        if not ok:
            logger.info("Rate limited user %s", uid)
            return

        for aid in sorted(self._admin_ids):
            await self._forward_one_admin(bot, message, uid, aid)

    async def _forward_one_admin(self, bot: Bot, message: Message, user_id: int, admin_id: int) -> None:
        try:
            fwd = await with_flood_wait(
                lambda a=admin_id: bot.forward_message(
                    chat_id=a,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
            )
            if fwd and getattr(message, "media_group_id", None):
                await self._redis.set(self._map_key(admin_id, fwd.message_id), str(user_id), ex=86400 * 7)
        except Forbidden:
            logger.warning("Cannot forward to admin %s (blocked bot or cannot DM)", admin_id)
        except TelegramError as e:
            logger.warning("Forward failed for admin %s: %s", admin_id, e)
            try:
                copied = await with_flood_wait(
                    lambda a=admin_id: bot.copy_message(
                        chat_id=a,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                )
                await self._redis.set(self._map_key(admin_id, copied.message_id), str(user_id), ex=86400 * 7)
            except TelegramError:
                text = (message.text or message.caption or "")[:3500]
                try:
                    await with_flood_wait(
                        lambda a=admin_id: bot.send_message(
                            chat_id=a,
                            text=f"[fallback] user `{user_id}`:\n{text}",
                            parse_mode="Markdown",
                        )
                    )
                except TelegramError:
                    logger.exception("Fallback DM failed for admin %s", admin_id)

    async def relay_admin_reply(self, bot: Bot, message: Message) -> bool:
        """
        If message is a reply from an admin's private inbox, deliver payload to end user.

        Returns True if handled.
        """
        if message.chat.type != ChatType.PRIVATE:
            return False
        if message.chat_id not in self._admin_ids:
            return False
        if message.from_user and message.from_user.id not in self._admin_ids:
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
            inbox_id = message.chat_id
            key = self._map_key(inbox_id, mid)
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
