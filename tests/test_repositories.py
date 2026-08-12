"""Persistence behaviour — the layer the old version did not have at all."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from indlab.db.models import DeliverableKind, OpenCall
from indlab.db.repo import (
    ArtistRepository,
    DeliverableRepository,
    OpenCallRepository,
    ReminderRepository,
    SubscriptionRepository,
)


async def test_get_or_create_is_idempotent(database):
    async with database.session() as session:
        repo = ArtistRepository(session)
        first = await repo.get_or_create(555, "Аня")
        second = await repo.get_or_create(555, "Аня")
        assert first.id == second.id


async def test_new_artist_gets_an_empty_profile(database):
    async with database.session() as session:
        repo = ArtistRepository(session)
        artist = await repo.get_or_create(556)
        profile = await repo.get_profile(artist.id)
        assert profile.completeness() == 0
        assert profile.as_prompt_block() == "(профиль пока пустой)"


async def test_profile_lists_merge_instead_of_overwriting(database, artist):
    async with database.session() as session:
        repo = ArtistRepository(session)
        await repo.update_profile(artist.artist_id, {"media": ["живопись"]})
        await repo.update_profile(artist.artist_id, {"media": ["коллаж"]})
        profile = await repo.get_profile(artist.artist_id)
    assert profile.media == ["живопись", "коллаж"]


async def test_profile_replace_overwrites(database, artist):
    async with database.session() as session:
        repo = ArtistRepository(session)
        await repo.update_profile(artist.artist_id, {"media": ["живопись"]})
        await repo.update_profile(artist.artist_id, {"media": ["видео"]}, replace=True)
        profile = await repo.get_profile(artist.artist_id)
    assert profile.media == ["видео"]


async def test_unknown_profile_field_is_kept_in_extra(database, artist):
    async with database.session() as session:
        repo = ArtistRepository(session)
        profile = await repo.update_profile(artist.artist_id, {"любимый_музей": "Прадо"})
    assert profile.extra["любимый_музей"] == "Прадо"


async def test_completeness_grows_with_answers(database, artist):
    async with database.session() as session:
        repo = ArtistRepository(session)
        before = (await repo.get_profile(artist.artist_id)).completeness()
        await repo.update_profile(
            artist.artist_id,
            {"full_name": "А. Иванова", "city": "Тбилиси", "media": ["графика"]},
        )
        after = (await repo.get_profile(artist.artist_id)).completeness()
    assert after > before


async def test_deliverable_versions_increment_and_demote_previous(database, artist):
    async with database.session() as session:
        repo = DeliverableRepository(session)
        first = await repo.save(artist.artist_id, DeliverableKind.STRATEGY, "первая")
        second = await repo.save(artist.artist_id, DeliverableKind.STRATEGY, "вторая")
        current = await repo.current(artist.artist_id, DeliverableKind.STRATEGY)
        history = await repo.history(artist.artist_id, DeliverableKind.STRATEGY)

    assert (first.version, second.version) == (1, 2)
    assert current.id == second.id
    assert len(history) == 2


async def test_deliverables_are_scoped_per_artist(database, artist, second_artist):
    async with database.session() as session:
        repo = DeliverableRepository(session)
        await repo.save(artist.artist_id, DeliverableKind.STRATEGY, "моя стратегия")
        other = await repo.current(second_artist.artist_id, DeliverableKind.STRATEGY)
    assert other is None


async def test_provenance_chain_is_recorded(database, artist):
    async with database.session() as session:
        repo = DeliverableRepository(session)
        report = await repo.save(artist.artist_id, DeliverableKind.PORTFOLIO_REPORT, "разбор")
        strategy = await repo.save(
            artist.artist_id, DeliverableKind.STRATEGY, "стратегия", based_on_id=report.id
        )
        plan = await repo.save(
            artist.artist_id, DeliverableKind.PLAN, "план", based_on_id=strategy.id
        )
    assert plan.based_on_id == strategy.id
    assert strategy.based_on_id == report.id


async def test_open_call_upsert_is_idempotent(database):
    payload = {"slug": "demo", "title": "Демо-опенкол", "disciplines": ["живопись"]}
    async with database.session() as session:
        repo = OpenCallRepository(session)
        await repo.upsert(payload)
        await repo.upsert({**payload, "title": "Демо-опенкол (обновлён)"})
        assert await repo.count() == 1
        call = await repo.get_by_slug("demo")
    assert call.title == "Демо-опенкол (обновлён)"


async def test_search_excludes_past_deadlines(database):
    today = date(2026, 6, 1)
    async with database.session() as session:
        repo = OpenCallRepository(session)
        await repo.upsert({"slug": "past", "title": "Прошедший", "deadline": date(2026, 5, 1)})
        await repo.upsert({"slug": "future", "title": "Будущий", "deadline": date(2026, 7, 1)})
        found = await repo.search(only_upcoming=True, today=today)
    assert [call.slug for call in found] == ["future"]


async def test_search_matches_description(database):
    async with database.session() as session:
        repo = OpenCallRepository(session)
        await repo.upsert(
            {"slug": "vid", "title": "Программа", "description": "Резиденция для видеоарта"}
        )
        found = await repo.search(query="видеоарт")
    assert len(found) == 1


async def test_subscribe_is_idempotent(database, artist):
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert({"slug": "s", "title": "S"})
        repo = SubscriptionRepository(session)
        await repo.subscribe(artist.artist_id, call.id)
        await repo.subscribe(artist.artist_id, call.id)
        assert len(await repo.list_for_artist(artist.artist_id)) == 1


@pytest.mark.parametrize("lead_days", [(30, 14, 7, 3, 1)])
async def test_reminders_skip_lead_times_already_in_the_past(database, artist, lead_days):
    now = datetime(2026, 6, 1, tzinfo=UTC)
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            # 10 days out: the 30- and 14-day nudges are already impossible.
            {"slug": "soon", "title": "Скоро", "deadline": (now + timedelta(days=10)).date()}
        )
        created = await ReminderRepository(session).schedule_for_call(
            artist.artist_id, call, lead_days, now=now
        )
    assert sorted(reminder.lead_days for reminder in created) == [1, 3, 7]


async def test_reminder_scheduling_is_idempotent(database, artist):
    now = datetime(2026, 6, 1, tzinfo=UTC)
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            {"slug": "x", "title": "X", "deadline": (now + timedelta(days=40)).date()}
        )
        repo = ReminderRepository(session)
        first = await repo.schedule_for_call(artist.artist_id, call, (30, 7), now=now)
        again = await repo.schedule_for_call(artist.artist_id, call, (30, 7), now=now)
    assert len(first) == 2
    assert again == []


async def test_reminders_without_a_deadline_are_not_scheduled(database, artist):
    async with database.session() as session:
        call = OpenCall(slug="nodate", title="Без даты")
        session.add(call)
        await session.flush()
        created = await ReminderRepository(session).schedule_for_call(
            artist.artist_id, call, (30, 7)
        )
    assert created == []


async def test_due_returns_only_unsent_and_past(database, artist):
    now = datetime(2026, 6, 1, tzinfo=UTC)
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            {"slug": "d", "title": "D", "deadline": (now + timedelta(days=40)).date()}
        )
        repo = ReminderRepository(session)
        await repo.schedule_for_call(artist.artist_id, call, (30, 7), now=now)
        # 30 days before a deadline 40 days away → fires in 10 days.
        assert await repo.due(now) == []
        due_later = await repo.due(now + timedelta(days=11))
    assert len(due_later) == 1


async def test_cancelling_removes_pending_reminders(database, artist):
    now = datetime(2026, 6, 1, tzinfo=UTC)
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            {"slug": "c", "title": "C", "deadline": (now + timedelta(days=40)).date()}
        )
        repo = ReminderRepository(session)
        await repo.schedule_for_call(artist.artist_id, call, (30, 7), now=now)
        cancelled = await repo.cancel_for_call(artist.artist_id, call.id)
        remaining = await repo.upcoming_for_artist(artist.artist_id)
    assert cancelled == 2
    assert remaining == []
