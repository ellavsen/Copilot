"""The bundled catalogue, text helpers and the profile interview parser."""

from __future__ import annotations

from datetime import date

import pytest

from indlab.bot.interview import QUESTIONS, next_question, parse_answer
from indlab.db.models import ArtistProfile, CareerStage
from indlab.db.repo import OpenCallRepository
from indlab.formatting import plural_days, split_message
from indlab.seeding import load_catalogue, next_occurrence, seed_open_calls


# ── catalogue ─────────────────────────────────────────────────────────
def test_catalogue_parses():
    rows = load_catalogue()
    assert len(rows) >= 10
    assert all(row["slug"] and row["title"] for row in rows)


def test_every_catalogue_deadline_is_marked_unverified():
    """Recurring calls move their dates; the copilot must never claim otherwise."""
    for row in load_catalogue():
        assert row["deadline_verified"] is False


def test_catalogue_entries_carry_a_link():
    missing = [row["slug"] for row in load_catalogue() if not row.get("url")]
    assert missing == [], f"entries without a source link: {missing}"


def test_catalogue_deadlines_are_in_the_future():
    today = date.today()
    for row in load_catalogue():
        if row["deadline"] is not None:
            assert row["deadline"] >= today, row["slug"]


@pytest.mark.parametrize(
    ("month_day", "today", "expected"),
    [
        ("03-06", date(2026, 1, 1), date(2026, 3, 6)),
        ("03-06", date(2026, 6, 1), date(2027, 3, 6)),
        ("02-29", date(2026, 1, 1), date(2028, 2, 29)),
    ],
)
def test_next_occurrence(month_day, today, expected):
    assert next_occurrence(month_day, today) == expected


def test_next_occurrence_rejects_garbage():
    assert next_occurrence("не дата") is None


async def test_seeding_is_idempotent(database):
    first = await seed_open_calls()
    await seed_open_calls()
    async with database.session() as session:
        total = await OpenCallRepository(session).count()
    assert total == first


async def test_seeded_catalogue_is_searchable_in_russian(database):
    """Regression: SQLite's lower() ignores Cyrillic, so search used to miss."""
    await seed_open_calls()
    async with database.session() as session:
        repo = OpenCallRepository(session)
        lower = await repo.search(query="резиденция")
        upper = await repo.search(query="РЕЗИДЕНЦИЯ")
        mixed = await repo.search(query="Резиденция")
    assert lower, "no match for a lowercase Cyrillic query"
    assert len(lower) == len(upper) == len(mixed)


async def test_search_by_discipline(database):
    await seed_open_calls()
    async with database.session() as session:
        found = await OpenCallRepository(session).search(query="видео")
    assert found


# ── text helpers ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "день"), (2, "дня"), (4, "дня"), (5, "дней"), (11, "дней"), (14, "дней"), (21, "день")],
)
def test_plural_days(count, expected):
    assert plural_days(count) == expected


def test_short_message_is_not_split():
    assert split_message("привет") == ["привет"]


def test_empty_message_yields_nothing():
    assert split_message("   ") == []


def test_long_message_splits_within_the_limit():
    text = "\n\n".join(f"Абзац номер {i}. " + "слово " * 40 for i in range(60))
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "Абзац номер 0" in chunks[0]
    assert "Абзац номер 59" in chunks[-1]


def test_split_handles_a_single_unbroken_line():
    chunks = split_message("я" * 9000)
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert sum(len(chunk) for chunk in chunks) == 9000


# ── interview parsing ─────────────────────────────────────────────────
def test_list_answers_are_split():
    assert parse_answer("media", "живопись, коллаж; видео") == ["живопись", "коллаж", "видео"]


def test_career_stage_is_mapped_from_natural_words():
    assert parse_answer("career_stage", "я только начинаю") == CareerStage.EMERGING.value
    assert parse_answer("career_stage", "Студентка") == CareerStage.STUDENT.value


def test_unrecognised_career_stage_returns_none():
    assert parse_answer("career_stage", "не знаю") is None


def test_links_are_labelled_by_platform():
    links = parse_answer("links", "https://instagram.com/me и https://mysite.ru")
    assert links["instagram"] == "https://instagram.com/me"
    assert "https://mysite.ru" in links.values()


def test_plain_text_answers_pass_through():
    assert parse_answer("city", "  Тбилиси ") == "Тбилиси"


def test_empty_answer_is_none():
    assert parse_answer("city", "   ") is None


def test_interview_walks_through_missing_fields():
    profile = ArtistProfile()
    first = next_question(profile)
    assert first is not None
    field, question = first
    assert question == QUESTIONS[field]

    # Once asked, the same field is not offered again.
    second = next_question(profile, asked={field})
    assert second is not None and second[0] != field


def test_interview_ends_when_nothing_is_missing():
    profile = ArtistProfile()
    assert next_question(profile, asked=set(ArtistProfile.INTERVIEW_FIELDS)) is None
