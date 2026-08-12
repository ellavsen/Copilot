"""Tools for reading and filling in the artist's own data.

This is the "personalisation" feature: the artist tells the copilot things
about themselves whenever it is convenient, the copilot remembers them
forever and reuses them in every later piece of advice and application form.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool

from indlab.agents.runtime import current_artist
from indlab.db.engine import get_database
from indlab.db.repo import ArtistRepository

FIELD_LABELS = {
    "full_name": "имя",
    "city": "город",
    "media": "медиум (живопись, графика, видео…)",
    "themes": "темы и мотивы",
    "career_stage": "этап карьеры",
    "statement": "artist statement",
    "goals": "цели на ближайший год",
    "audience": "аудитория",
    "education": "образование",
    "exhibitions": "выставки",
    "price_range": "ценовой диапазон работ",
    "links": "ссылки (сайт, соцсети)",
}


@tool
async def get_artist_profile() -> str:
    """Read everything the artist has told us about themselves so far.

    Use this before giving any personalised advice, writing an artist
    statement, or filling in an application. Returns the stored profile, how
    complete it is, and which details are still missing.
    """
    artist = current_artist()
    async with get_database().session() as session:
        profile = await ArtistRepository(session).get_profile(artist.artist_id)
        block = profile.as_prompt_block()
        completeness = profile.completeness()
        missing = profile.missing_fields()

    lines = [f"Профиль художника (заполнен на {completeness}%):", block]
    if missing:
        readable = ", ".join(FIELD_LABELS.get(name, name) for name in missing[:5])
        lines.append(f"\nПока не заполнено: {readable}.")
    return "\n".join(lines)


@tool
async def update_artist_profile(
    full_name: str | None = None,
    city: str | None = None,
    country: str | None = None,
    birth_year: int | None = None,
    media: list[str] | None = None,
    themes: list[str] | None = None,
    career_stage: Literal["student", "emerging", "mid_career", "established"] | None = None,
    statement: str | None = None,
    education: str | None = None,
    exhibitions: str | None = None,
    awards: str | None = None,
    publications: str | None = None,
    goals: list[str] | None = None,
    audience: str | None = None,
    price_range: str | None = None,
    languages: list[str] | None = None,
    links: dict[str, str] | None = None,
) -> str:
    """Save details the artist just told you about themselves.

    Call this whenever the artist mentions a fact about their practice, career
    or goals — even in passing, and even if they did not ask you to remember
    it. Pass only the fields you actually learned; everything else stays as it
    was. Lists (media, themes, goals, languages) and links are merged with
    what is already stored, never overwritten.
    """
    artist = current_artist()
    updates = {
        "full_name": full_name,
        "city": city,
        "country": country,
        "birth_year": birth_year,
        "media": media,
        "themes": themes,
        "career_stage": career_stage,
        "statement": statement,
        "education": education,
        "exhibitions": exhibitions,
        "awards": awards,
        "publications": publications,
        "goals": goals,
        "audience": audience,
        "price_range": price_range,
        "languages": languages,
        "links": links,
    }
    updates = {key: value for key, value in updates.items() if value not in (None, "", [], {})}
    if not updates:
        return "Нечего сохранять — не передано ни одного поля."

    async with get_database().session() as session:
        profile = await ArtistRepository(session).update_profile(artist.artist_id, updates)
        completeness = profile.completeness()
        missing = profile.missing_fields()

    saved = ", ".join(FIELD_LABELS.get(key, key) for key in updates)
    result = f"✅ Записала: {saved}. Профиль заполнен на {completeness}%."
    if missing:
        result += f" Ещё пригодилось бы: {FIELD_LABELS.get(missing[0], missing[0])}."
    return result


PROFILE_TOOLS = [get_artist_profile, update_artist_profile]
