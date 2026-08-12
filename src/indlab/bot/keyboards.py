"""Reply keyboards and the button → intent mapping."""

from __future__ import annotations

from telegram import ReplyKeyboardMarkup

BTN_PORTFOLIO = "🎨 Разобрать портфолио"
BTN_STRATEGY = "📈 Стратегия"
BTN_PLAN = "🗓 План"
BTN_OPEN_CALLS = "🔔 Опенколы"
BTN_PROFILE = "👤 Мой профиль"
BTN_HELP = "❓ Что ты умеешь"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_PORTFOLIO, BTN_STRATEGY],
        [BTN_PLAN, BTN_OPEN_CALLS],
        [BTN_PROFILE, BTN_HELP],
    ],
    resize_keyboard=True,
)

INTERVIEW_KEYBOARD = ReplyKeyboardMarkup(
    [["Пропустить"], ["Хватит на сегодня"]],
    resize_keyboard=True,
    one_time_keyboard=False,
)

# Buttons that are really just a phrase for the agent team. Keeping them as
# natural language means the supervisor routes them exactly as it routes typed
# questions — one code path, not two.
BUTTON_INTENTS: dict[str, str] = {
    BTN_PORTFOLIO: "Разбери, пожалуйста, моё портфолио.",
    BTN_STRATEGY: "Собери для меня маркетинговую стратегию.",
    BTN_PLAN: "Составь мне план продвижения на ближайшие месяцы.",
    BTN_OPEN_CALLS: "Какие опенколы мне сейчас подойдут?",
}
