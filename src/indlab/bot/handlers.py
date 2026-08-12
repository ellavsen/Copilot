"""Telegram handlers.

Identity note: there is no registration and no login. The artist *is* their
Telegram account, so no password ever travels through a chat and nothing
sensitive ends up in the message history.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes, ConversationHandler

from indlab.agents.runtime import ArtistContext, artist_scope
from indlab.bot import keyboards as kb
from indlab.bot.interview import QUESTIONS, SKIP, STOP, next_question, parse_answer
from indlab.db.engine import get_database
from indlab.db.models import DeliverableKind
from indlab.db.repo import ArtistRepository, DeliverableRepository, SubscriptionRepository
from indlab.formatting import split_message
from indlab.tools.opencalls import format_open_call

log = logging.getLogger(__name__)

ASKING = 1

WELCOME = """
🎨 Привет! Я — копайлот для художника.

Я беру на себя всё, что вокруг творчества, чтобы ты могла заниматься самим творчеством:

• разберу портфолио и artist statement — честно, по делу
• соберу маркетинговую стратегию под твою практику
• составлю выполнимый план продвижения
• буду следить за опенколами и напоминать о дедлайнах
• запомню всё, что ты о себе рассказываешь, чтобы ты не повторялась

Начать проще всего с профиля — /profile. Чем больше я о тебе знаю, тем
конкретнее советы.

Или просто напиши мне вопрос своими словами.
""".strip()

HELP = """
Что я умею:

/profile — показать, что я о тебе знаю
/fill — заполнить или дополнить профиль по шагам
/progress — на каком этапе мы сейчас
/opencalls — за какими опенколами я слежу
/reset — начать разговор с чистого листа
/help — это сообщение

А ещё просто пиши мне обычным текстом:
«что не так с моим statement», «куда подать работы про телесность»,
«сколько стоит моя графика» — я разберусь, к кому из специалистов
переадресовать вопрос.
""".strip()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
async def _resolve_artist(update: Update) -> ArtistContext:
    """Look up (or create) the artist behind this chat."""
    user = update.effective_user
    chat = update.effective_chat
    telegram_id = user.id if user else chat.id
    name = user.full_name if user else None

    async with get_database().session() as session:
        artist = await ArtistRepository(session).get_or_create(telegram_id, name)
        return ArtistContext(
            artist_id=artist.id, telegram_id=telegram_id, display_name=artist.display_name
        )


async def _reply(update: Update, text: str, **kwargs: object) -> None:
    """Send a possibly long answer as several Telegram-sized messages."""
    chunks = split_message(text)
    if not chunks:
        return
    for chunk in chunks[:-1]:
        await update.effective_message.reply_text(chunk)
    await update.effective_message.reply_text(chunks[-1], **kwargs)


@contextlib.asynccontextmanager
async def _typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keep the "typing…" indicator alive while a slow answer is produced."""
    chat_id = update.effective_chat.id

    async def beat() -> None:
        while True:
            with contextlib.suppress(Exception):
                await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ─────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_artist(update)
    await update.effective_message.reply_text(WELCOME, reply_markup=kb.MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP, reply_markup=kb.MAIN_KEYBOARD)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    artist = await _resolve_artist(update)
    async with get_database().session() as session:
        profile = await ArtistRepository(session).get_profile(artist.artist_id)
        block = profile.as_prompt_block()
        percent = profile.completeness()
        missing = profile.missing_fields()

    text = f"👤 Твой профиль — заполнен на {percent}%\n\n{block}"
    if missing:
        readable = ", ".join(QUESTIONS[field].split("?")[0][:40] for field in missing[:3])
        text += f"\n\nЕщё можно рассказать: {readable.lower()}…\nЗаполнить по шагам — /fill"
    else:
        text += "\n\nПрофиль полный — с таким я могу советовать предметно."
    await _reply(update, text, reply_markup=kb.MAIN_KEYBOARD)


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    artist = await _resolve_artist(update)
    labels = {
        DeliverableKind.PORTFOLIO_REPORT: ("Разбор портфолио", "Разбери моё портфолио"),
        DeliverableKind.STRATEGY: ("Маркетинговая стратегия", "Собери мне стратегию"),
        DeliverableKind.PLAN: ("План продвижения", "Составь мне план"),
    }
    async with get_database().session() as session:
        chain = await DeliverableRepository(session).latest_chain(artist.artist_id)

    lines = ["📍 Где мы сейчас:\n"]
    next_step: str | None = None
    for kind, (label, suggestion) in labels.items():
        deliverable = chain.get(kind)
        if deliverable is None:
            lines.append(f"⬜ {label}")
            next_step = next_step or suggestion
        else:
            lines.append(f"✅ {label} — версия {deliverable.version}")
    if next_step:
        lines.append(f"\nСледующий шаг: напиши «{next_step}».")
    else:
        lines.append("\nВсе три шага пройдены. Дальше — опенколы: /opencalls")
    await _reply(update, "\n".join(lines), reply_markup=kb.MAIN_KEYBOARD)


async def opencalls_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    artist = await _resolve_artist(update)
    async with get_database().session() as session:
        subscriptions = await SubscriptionRepository(session).list_for_artist(artist.artist_id)
        blocks = [format_open_call(subscription.open_call) for subscription in subscriptions]

    if not blocks:
        await update.effective_message.reply_text(
            "Пока я ни за чем не слежу.\n\n"
            "Напиши, что тебе интересно — например «резиденции в Европе» или "
            "«гранты для видеоарта», — и я подберу и поставлю напоминания.",
            reply_markup=kb.MAIN_KEYBOARD,
        )
        return
    await _reply(
        update,
        "🔔 Я слежу за этим:\n\n" + "\n\n".join(blocks),
        reply_markup=kb.MAIN_KEYBOARD,
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forget the conversation thread, but keep the profile and deliverables."""
    artist = await _resolve_artist(update)
    context.user_data["thread_suffix"] = context.user_data.get("thread_suffix", 0) + 1
    log.info("Reset conversation for artist %s", artist.artist_id)
    await update.effective_message.reply_text(
        "Начинаем разговор заново. Профиль, разборы и напоминания на месте — "
        "я забыла только ход беседы.",
        reply_markup=kb.MAIN_KEYBOARD,
    )


# ─────────────────────────────────────────────────────────────────────
# Profile interview
# ─────────────────────────────────────────────────────────────────────
async def fill_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    artist = await _resolve_artist(update)
    context.user_data["asked"] = set()
    context.user_data["artist_id"] = artist.artist_id

    async with get_database().session() as session:
        profile = await ArtistRepository(session).get_profile(artist.artist_id)
        question = next_question(profile, context.user_data["asked"])

    if question is None:
        await update.effective_message.reply_text(
            "Профиль уже заполнен. Если что-то изменилось — просто скажи мне об этом словами.",
            reply_markup=kb.MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    field, text = question
    context.user_data["pending_field"] = field
    await update.effective_message.reply_text(
        "Задам несколько вопросов. Любой можно пропустить — "
        f"кнопка «{SKIP}». Остановиться — «{STOP}».\n\n{text}",
        reply_markup=kb.INTERVIEW_KEYBOARD,
    )
    return ASKING


async def fill_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = (update.effective_message.text or "").strip()
    field = context.user_data.get("pending_field")
    artist_id = context.user_data.get("artist_id")
    asked: set[str] = context.user_data.setdefault("asked", set())

    if answer == STOP or artist_id is None:
        return await _finish_interview(update, context)

    if field and answer != SKIP:
        value = parse_answer(field, answer)
        if value is None and field == "career_stage":
            await update.effective_message.reply_text(
                "Не поняла этап. Одним словом: студент, начинаю, в процессе "
                f"или состоявшийся. Либо «{SKIP}»."
            )
            return ASKING
        if value not in (None, "", [], {}):
            async with get_database().session() as session:
                await ArtistRepository(session).update_profile(artist_id, {field: value})

    if field:
        asked.add(field)

    async with get_database().session() as session:
        profile = await ArtistRepository(session).get_profile(artist_id)
        question = next_question(profile, asked)
        percent = profile.completeness()

    if question is None:
        return await _finish_interview(update, context)

    next_field, text = question
    context.user_data["pending_field"] = next_field
    await update.effective_message.reply_text(
        f"({percent}% готово)\n\n{text}", reply_markup=kb.INTERVIEW_KEYBOARD
    )
    return ASKING


async def _finish_interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    artist_id = context.user_data.get("artist_id")
    percent = 0
    if artist_id:
        async with get_database().session() as session:
            profile = await ArtistRepository(session).get_profile(artist_id)
            percent = profile.completeness()

    for key in ("pending_field", "asked", "artist_id"):
        context.user_data.pop(key, None)

    await update.effective_message.reply_text(
        f"Записала. Профиль заполнен на {percent}%.\n\n"
        "Можно продолжить в любой момент — /fill. А сейчас попробуй «Разобрать портфолио».",
        reply_markup=kb.MAIN_KEYBOARD,
    )
    return ConversationHandler.END


async def fill_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("pending_field", "asked", "artist_id"):
        context.user_data.pop(key, None)
    await update.effective_message.reply_text("Прервались.", reply_markup=kb.MAIN_KEYBOARD)
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────
# Free-form chat → the agent team
# ─────────────────────────────────────────────────────────────────────
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return

    if text == kb.BTN_PROFILE:
        await profile_command(update, context)
        return
    if text == kb.BTN_HELP:
        await help_command(update, context)
        return
    text = kb.BUTTON_INTENTS.get(text, text)

    artist = await _resolve_artist(update)
    copilot = context.application.bot_data["copilot"]
    suffix = context.user_data.get("thread_suffix", 0)
    thread_id = f"artist-{artist.artist_id}-{suffix}"

    async with _typing(update, context):
        # Everything the tools need to know about *who* is asking is bound
        # here, not passed through the model.
        with artist_scope(artist):
            answer = await copilot.ask(text, thread_id=thread_id)

    await _reply(update, answer, reply_markup=kb.MAIN_KEYBOARD)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error while processing an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "Что-то сломалось на моей стороне. Попробуй ещё раз чуть позже.",
                reply_markup=ReplyKeyboardRemove(),
            )
