"""Register python-telegram-bot handlers and filters."""

from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from configs.settings import Settings
from handlers import admin_fsm_private, channel_handlers, join_request_handlers, user_handlers


def register_handlers(application: Application, settings: Settings) -> None:
    """Attach all handlers with priority groups (lower runs first)."""

    # ENV-listed admin replies in private chat (handler no-ops for others)
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.REPLY & ~filters.COMMAND,
            user_handlers.admin_inbox_reply,
        ),
        group=-1,
    )

    application.add_handler(
        ChatMemberHandler(channel_handlers.on_chat_member, chat_member_types=ChatMemberHandler.CHAT_MEMBER)
    )
    application.add_handler(ChatJoinRequestHandler(join_request_handlers.on_chat_join_request))
    application.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, channel_handlers.on_channel_post)
    )

    application.add_handler(CommandHandler("start", user_handlers.cmd_start, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", user_handlers.cmd_help, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("admin", user_handlers.cmd_admin, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("cancel", user_handlers.cmd_cancel, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("skip", user_handlers.cmd_wizard_skip, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("done", user_handlers.cmd_done_router, filters=filters.ChatType.PRIVATE))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            admin_fsm_private.admin_fsm_private,
        )
    )

    application.add_handler(CallbackQueryHandler(user_handlers.any_callback))

    # Group 1: must run *after* group-0 `admin_fsm_private` (PTB allows only one handler
    # per group; the FSM handler always matched first and blocked inbox forwarding).
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            user_handlers.any_private_message,
        ),
        group=1,
    )
