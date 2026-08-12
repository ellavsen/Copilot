"""The guided profile interview.

The artist can always just talk to the copilot and it will remember what it
hears. This is the deliberate version of the same thing: a short, skippable
questionnaire for people who would rather fill the form once and be done.
"""

from __future__ import annotations

import re
from typing import Any

from indlab.db.models import ArtistProfile, CareerStage

SKIP = "Пропустить"
STOP = "Хватит на сегодня"

QUESTIONS: dict[str, str] = {
    "full_name": "Как тебя зовут как художника — имя, под которым ты подписываешь работы?",
    "city": "В каком городе ты сейчас работаешь?",
    "media": (
        "В каких техниках и медиумах работаешь?\n"
        "Перечисли через запятую — например: живопись, коллаж, видео."
    ),
    "themes": (
        "О чём твои работы? Назови 2–3 темы или мотива через запятую.\n"
        "Например: память, телесность, городская среда."
    ),
    "career_stage": (
        "На каком ты этапе?\nОтветь одним словом: студент / начинаю / в процессе / состоявшийся."
    ),
    "statement": (
        "Пришли свой artist statement, если он есть — можно черновиком.\n"
        "Если его нет, напиши 3–4 предложения о своей практике своими словами: "
        "я помогу довести до ума."
    ),
    "goals": (
        "Какие цели на ближайший год? Через запятую.\n"
        "Например: первая персональная выставка, продавать стабильно, попасть в резиденцию."
    ),
    "audience": "Кто твой зритель и покупатель, как ты это себе представляешь?",
    "education": "Художественное образование — где и когда? Если самоучка, так и напиши.",
    "exhibitions": "Где уже выставлялся? Перечисли самое важное, без полного списка.",
    "price_range": "В каком диапазоне продаются твои работы? Можно вилкой и в любой валюте.",
    "links": "Пришли ссылки — сайт, соцсети, портфолио. Можно просто списком.",
}

CAREER_WORDS: dict[str, CareerStage] = {
    "студент": CareerStage.STUDENT,
    "студентка": CareerStage.STUDENT,
    "учусь": CareerStage.STUDENT,
    "начинаю": CareerStage.EMERGING,
    "начинающий": CareerStage.EMERGING,
    "начинающая": CareerStage.EMERGING,
    "новичок": CareerStage.EMERGING,
    "emerging": CareerStage.EMERGING,
    "в процессе": CareerStage.MID_CAREER,
    "середина": CareerStage.MID_CAREER,
    "опытный": CareerStage.MID_CAREER,
    "опытная": CareerStage.MID_CAREER,
    "состоявшийся": CareerStage.ESTABLISHED,
    "состоявшаяся": CareerStage.ESTABLISHED,
    "известный": CareerStage.ESTABLISHED,
}

LIST_FIELDS = {"media", "themes", "goals", "languages"}

_URL = re.compile(r"(https?://\S+|(?:www\.)\S+\.\S+|@[\w.]+)")

_PLATFORMS = {
    "instagram": "instagram",
    "behance": "behance",
    "vk.com": "vk",
    "t.me": "telegram",
    "telegram": "telegram",
    "artstation": "artstation",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "facebook": "facebook",
    "x.com": "x",
    "twitter": "x",
}


def _split_list(text: str) -> list[str]:
    parts = re.split(r"[,;\n•]+", text)
    return [part.strip() for part in parts if part.strip()]


def _parse_links(text: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for index, raw in enumerate(_URL.findall(text)):
        url = raw.strip().rstrip(".,;")
        label = next(
            (name for domain, name in _PLATFORMS.items() if domain in url.lower()),
            None,
        )
        if label is None:
            label = "сайт" if index == 0 else f"ссылка {index + 1}"
        links[label] = url
    if not links and text.strip():
        links["сайт"] = text.strip()
    return links


def parse_answer(field: str, text: str) -> Any:
    """Turn a free-form chat answer into something the model can store."""
    text = text.strip()
    if not text:
        return None
    if field in LIST_FIELDS:
        return _split_list(text)
    if field == "links":
        return _parse_links(text)
    if field == "career_stage":
        lowered = text.lower()
        for word, stage in CAREER_WORDS.items():
            if word in lowered:
                return stage.value
        return None
    return text


def next_question(profile: ArtistProfile, asked: set[str] | None = None) -> tuple[str, str] | None:
    """Return the next ``(field, question)`` to ask, or ``None`` when done."""
    asked = asked or set()
    for field in profile.missing_fields():
        if field in asked:
            continue
        question = QUESTIONS.get(field)
        if question:
            return field, question
    return None
