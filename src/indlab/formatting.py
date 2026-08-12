"""Small text helpers shared by the bot, the tools and the reminder service."""

from __future__ import annotations

# Telegram rejects messages longer than 4096 characters.
TELEGRAM_LIMIT = 4096


def plural_days(count: int) -> str:
    """Russian plural for "день" — 1 день, 2 дня, 5 дней, 11 дней."""
    count = abs(count)
    if 11 <= count % 100 <= 14:
        return "дней"
    return {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(count % 10, "дней")


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split a long answer into Telegram-sized chunks on natural boundaries.

    Prefers paragraph breaks, then line breaks, and only cuts mid-line when a
    single line is itself longer than the limit.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return [chunk for chunk in chunks if chunk]
