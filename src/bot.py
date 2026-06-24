"""
Upload Notifier Bot — Entry Point.
Fixes: log path, cancel lambda coroutine, job timing, text router order, WAITING_BROADCAST import.
"""

import logging
import sys
import os

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler,
)
from config import BOT_TOKEN, OWNER_ID
from handlers.owner import (
    start, assign_task, handle_task_text, handle_priority,
    handle_deadline_text, handle_deadline_skip, handle_admin_selection,
    verify_task, handle_verification, handle_reject_reason,
    list_admins, add_admin_cmd, handle_add_admin,
    remove_admin_cmd, handle_remove_admin_btn,
    broadcast_cmd, handle_broadcast,
    stats_cmd, stats_callback,
)
from handlers.admin import (
    my_tasks, mark_done, view_uploads,
    add_note_prompt, receive_note,
    uploads_callback, my_tasks_btn,
)
from handlers.scraper import start_scraper, scrape_startup
from handlers.dashboard import dashboard, dashboard_filter
from states import (
    WAITING_TASK_TEXT, WAITING_ADMIN_SELECT, WAITING_ADD_ADMIN,
    WAITING_PRIORITY, WAITING_DEADLINE, WAITING_BROADCAST,
)

# ─── LOGGING ────────────────────────────────────────────────────
# Use absolute path so the log file is always created in data/ regardless
# of which directory the bot is launched from.
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(_DATA_DIR, exist_ok=True)
_LOG_PATH = os.path.join(_DATA_DIR, "bot.log")

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─── BOT COMMAND LISTS ──────────────────────────────────────────

OWNER_COMMANDS = [
    BotCommand("start",       "🏠 Home dashboard"),
    BotCommand("assign",      "📋 Assign task to admin"),
    BotCommand("dashboard",   "📊 Task board with filters"),
    BotCommand("stats",       "📈 Bot statistics"),
    BotCommand("admins",      "👥 List all admins"),
    BotCommand("addadmin",    "➕ Add new admin"),
    BotCommand("removeadmin", "🗑 Remove an admin"),
    BotCommand("broadcast",   "📢 Message all admins"),
    BotCommand("uploads",     "🎬 Latest 1TamilMV uploads"),
]

ADMIN_COMMANDS = [
    BotCommand("start",   "🏠 Home"),
    BotCommand("mytasks", "📋 My assigned tasks"),
    BotCommand("uploads", "🎬 Latest 1TamilMV uploads"),
]

DEFAULT_COMMANDS = [
    BotCommand("start", "🏠 Start the bot"),
]


# ─── STARTUP HOOK ───────────────────────────────────────────────

async def register_commands(app: Application) -> None:
    """Set role-aware bot command menus for owner, each admin, and default."""
    import database as db

    await app.bot.set_my_commands(DEFAULT_COMMANDS, scope=BotCommandScopeDefault())

    try:
        await app.bot.set_my_commands(
            OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
        logger.info("Owner commands registered for %s", OWNER_ID)
    except Exception as exc:
        logger.warning("Could not register owner commands: %s", exc)

    for admin in db.get_all_admins():
        try:
            await app.bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin["user_id"])
            )
        except Exception as exc:
            logger.warning("Could not register commands for admin %s: %s", admin["user_id"], exc)

    logger.info("Bot command menus registered.")


async def on_startup(app: Application) -> None:
    await register_commands(app)
    # Startup digest fires 5s after polling starts (bot is fully ready by then).
    # Recurring scraper starts at 305s so it doesn't duplicate the startup digest.
    app.job_queue.run_once(scrape_startup, when=5)
    logger.info("🤖 Upload Notifier Bot is live!")


# ─── CANCEL HELPER ──────────────────────────────────────────────

async def _cancel(update: Update, context) -> int:
    """Async cancel handler for ConversationHandler fallbacks."""
    await update.message.reply_text("❌ Cancelled\\.", parse_mode="MarkdownV2")
    return ConversationHandler.END


# ─── FREE-TEXT ROUTERS ──────────────────────────────────────────

async def _owner_text_router(update: Update, context) -> None:
    """Route free-text from owner when not inside a ConversationHandler state."""
    if context.user_data.get("awaiting_reject_reason"):
        await handle_reject_reason(update, context)


async def _admin_text_router(update: Update, context) -> None:
    """Route free-text from admins (note entry)."""
    if context.user_data.get("awaiting_note"):
        await receive_note(update, context)


# ─── MAIN ───────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN is not set. Edit .env and restart.")
        sys.exit(1)
    if not OWNER_ID:
        logger.error("OWNER_ID is not set. Edit .env and restart.")
        sys.exit(1)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    # ── Conversation: assign task ────────────────────────────────
    assign_conv = ConversationHandler(
        entry_points=[CommandHandler("assign", assign_task)],
        states={
            WAITING_TASK_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task_text)
            ],
            WAITING_PRIORITY: [
                CallbackQueryHandler(handle_priority, pattern="^priority_"),
            ],
            WAITING_DEADLINE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deadline_text),
                CallbackQueryHandler(handle_deadline_skip, pattern="^deadline_skip$"),
            ],
            WAITING_ADMIN_SELECT: [
                CallbackQueryHandler(handle_admin_selection, pattern="^select_admin_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        per_user=True,
        allow_reentry=True,
    )

    # ── Conversation: add admin ──────────────────────────────────
    add_admin_conv = ConversationHandler(
        entry_points=[CommandHandler("addadmin", add_admin_cmd)],
        states={
            WAITING_ADD_ADMIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_admin)
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        per_user=True,
        allow_reentry=True,
    )

    # ── Conversation: broadcast ──────────────────────────────────
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_cmd)],
        states={
            WAITING_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        per_user=True,
        allow_reentry=True,
    )

    # ── Commands ─────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("mytasks",     my_tasks))
    app.add_handler(CommandHandler("admins",      list_admins))
    app.add_handler(CommandHandler("uploads",     view_uploads))
    app.add_handler(CommandHandler("dashboard",   dashboard))
    app.add_handler(CommandHandler("stats",       stats_cmd))
    app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))

    # ── Conversations ────────────────────────────────────────────
    app.add_handler(assign_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(broadcast_conv)

    # ── Inline callbacks ─────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(mark_done,               pattern="^done_task_"))
    app.add_handler(CallbackQueryHandler(verify_task,             pattern="^verify_task_"))
    app.add_handler(CallbackQueryHandler(handle_verification,     pattern="^(approve|reject)_task_"))
    app.add_handler(CallbackQueryHandler(dashboard_filter,        pattern="^dash_filter_"))
    app.add_handler(CallbackQueryHandler(stats_callback,          pattern="^show_stats$"))
    app.add_handler(CallbackQueryHandler(uploads_callback,        pattern="^(show_uploads|uploads_page_)"))
    app.add_handler(CallbackQueryHandler(add_note_prompt,         pattern="^add_note_"))
    app.add_handler(CallbackQueryHandler(handle_remove_admin_btn, pattern="^rm_admin_"))
    app.add_handler(CallbackQueryHandler(my_tasks_btn,            pattern="^my_tasks_btn$"))

    # ── Free-text routers (OWNER first so it takes priority) ─────
    # Group 1: owner — only fires when NOT inside a ConversationHandler state
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(OWNER_ID),
            _owner_text_router,
        ),
        group=1,
    )
    # Group 1: admins
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _admin_text_router),
        group=1,
    )

    # ── Recurring scraper (starts at 305s to avoid duplicating startup digest) ──
    app.job_queue.run_repeating(start_scraper, interval=300, first=305)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
