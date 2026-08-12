"""Telegram application wiring and lifecycle.

Everything expensive — the database, the catalogue, the agent graph, the
reminder loop — is created once at start-up and torn down cleanly on exit.
"""

from __future__ import annotations

import logging

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from indlab.agents.graph import build_copilot
from indlab.bot import handlers
from indlab.config import get_settings
from indlab.db.engine import get_database
from indlab.reminders.service import ReminderService
from indlab.seeding import seed_open_calls

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand("start", "Начать"),
    BotCommand("profile", "Мой профиль"),
    BotCommand("fill", "Заполнить профиль по шагам"),
    BotCommand("progress", "На каком я этапе"),
    BotCommand("opencalls", "Мои опенколы"),
    BotCommand("reset", "Начать разговор заново"),
    BotCommand("help", "Что ты умеешь"),
]


async def _post_init(application: Application) -> None:
    settings = get_settings()

    database = get_database()
    await database.create_all()
    await seed_open_calls()

    # Conversation memory, keyed by thread id, surviving restarts.
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(str(settings.checkpoint_path))
    checkpointer = AsyncSqliteSaver(connection)
    await checkpointer.setup()

    application.bot_data["copilot"] = build_copilot(checkpointer=checkpointer)
    application.bot_data["checkpoint_conn"] = connection

    async def send(telegram_id: int, text: str) -> None:
        await application.bot.send_message(chat_id=telegram_id, text=text)

    reminders = ReminderService(send)
    reminders.start()
    application.bot_data["reminders"] = reminders

    await application.bot.set_my_commands(COMMANDS)
    log.info("Copilot is up")


async def _post_shutdown(application: Application) -> None:
    reminders: ReminderService | None = application.bot_data.get("reminders")
    if reminders is not None:
        await reminders.stop()

    connection = application.bot_data.get("checkpoint_conn")
    if connection is not None:
        await connection.close()

    await get_database().dispose()
    log.info("Copilot shut down cleanly")


def build_application() -> Application:
    """Assemble the Telegram application with all handlers registered."""
    settings = get_settings()
    application = (
        ApplicationBuilder()
        .token(settings.require_bot_token())
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    interview = ConversationHandler(
        entry_points=[CommandHandler("fill", handlers.fill_start)],
        states={
            handlers.ASKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.fill_answer)]
        },
        fallbacks=[CommandHandler("cancel", handlers.fill_cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("profile", handlers.profile_command))
    application.add_handler(CommandHandler("progress", handlers.progress_command))
    application.add_handler(CommandHandler("opencalls", handlers.opencalls_command))
    application.add_handler(CommandHandler("reset", handlers.reset_command))
    application.add_handler(interview)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.chat))
    application.add_error_handler(handlers.on_error)

    return application


def run() -> None:
    """Start long polling. Blocks until interrupted."""
    application = build_application()
    log.info("Starting long polling …")
    application.run_polling(drop_pending_updates=True)


__all__ = ["build_application", "run"]
