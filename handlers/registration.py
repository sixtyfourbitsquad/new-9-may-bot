"""Register python-telegram-bot handlers and filters."""

from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from configs.settings import Settings
from handlers import admin_fsm_private, channel_handlers, user_handlers


def register_handlers(application: Application, settings: Settings) -> None:
    """Attach all handlers with priority groups (lower runs first)."""

    # Private inbox for each admin user id (same as user id in private chat with bot)
    admin_inbox_filter = filters.Chat(chat_id=tuple(settings.admin_user_ids))

    application.add_handler(
        MessageHandler(admin_inbox_filter & filters.REPLY, user_handlers.admin_inbox_reply),
        group=-1,
    )

    application.add_handler(
        ChatMemberHandler(channel_handlers.on_chat_member, chat_member_types=ChatMemberHandler.CHAT_MEMBER)
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, channel_handlers.on_channel_post)
    )

    application.add_handler(CommandHandler("start", user_handlers.cmd_start, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", user_handlers.cmd_help, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("admin", user_handlers.cmd_admin, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("cancel", user_handlers.cmd_cancel, filters=filters.ChatType.PRIVATE))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            admin_fsm_private.admin_fsm_private,
        )
    )

    application.add_handler(CallbackQueryHandler(user_handlers.any_callback))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            user_handlers.any_private_message,
        )
    )
