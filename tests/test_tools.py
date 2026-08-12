"""Agent tools.

The headline case is ``test_plan_is_refused_without_a_strategy``: in the old
build ``MarketingStrategyRetriever`` always replied "стратегия загружена", so
the planner's guard could never fire and plans were written on top of nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from indlab.agents.runtime import NoArtistContextError
from indlab.db.models import DeliverableKind
from indlab.db.repo import DeliverableRepository, OpenCallRepository, ReminderRepository
from indlab.tools.deliverables import (
    get_artist_progress,
    get_marketing_strategy,
    get_portfolio_material,
    save_marketing_strategy,
    save_portfolio_report,
    save_promotion_plan,
)
from indlab.tools.knowledge import search_art_knowledge
from indlab.tools.opencalls import (
    add_open_call,
    list_tracked_open_calls,
    match_open_calls_to_profile,
    search_open_calls,
    track_open_call,
    untrack_open_call,
)
from indlab.tools.profile import get_artist_profile, update_artist_profile


# ── identity is not something the model can choose ────────────────────
async def test_tools_refuse_to_run_without_an_artist_scope(database):
    with pytest.raises(NoArtistContextError):
        await get_artist_profile.ainvoke({})


async def test_tool_writes_to_the_scoped_artist_only(database, artist, second_artist):
    from indlab.agents.runtime import artist_scope

    with artist_scope(artist):
        await update_artist_profile.ainvoke({"city": "Ереван"})
    with artist_scope(second_artist):
        result = await get_artist_profile.ainvoke({})
    assert "Ереван" not in result


# ── profile ───────────────────────────────────────────────────────────
async def test_update_profile_reports_what_it_saved(scope):
    result = await update_artist_profile.ainvoke(
        {"full_name": "Мария К.", "media": ["графика", "коллаж"]}
    )
    assert "✅" in result
    assert "имя" in result

    stored = await get_artist_profile.ainvoke({})
    assert "Мария К." in stored
    assert "графика" in stored


async def test_update_profile_with_nothing_is_a_no_op(scope):
    assert "Нечего сохранять" in await update_artist_profile.ainvoke({})


async def test_profile_reports_missing_fields(scope):
    assert "Пока не заполнено" in await get_artist_profile.ainvoke({})


# ── the report → strategy → plan chain ────────────────────────────────
async def test_plan_is_refused_without_a_strategy(scope, database):
    result = await save_promotion_plan.ainvoke({"plan": "неделя 1: сделать сайт"})
    assert "❌" in result

    async with database.session() as session:
        stored = await DeliverableRepository(session).current(scope.artist_id, DeliverableKind.PLAN)
    assert stored is None, "a plan must not be persisted without a strategy"


async def test_strategy_lookup_is_honest_when_empty(scope):
    assert "СТРАТЕГИИ НЕТ" in await get_marketing_strategy.ainvoke({})


async def test_full_chain_persists_and_links(scope, database):
    await save_portfolio_report.ainvoke(
        {"report": "разбор портфолио", "strengths": ["цвет"], "recommendations": ["серия"]}
    )
    await save_marketing_strategy.ainvoke(
        {"strategy": "стратегия", "target_audience": "молодые коллекционеры"}
    )
    result = await save_promotion_plan.ainvoke({"plan": "план", "steps": ["шаг 1"]})
    assert "✅" in result

    async with database.session() as session:
        repo = DeliverableRepository(session)
        chain = await repo.latest_chain(scope.artist_id)
        plan = chain[DeliverableKind.PLAN]
        strategy = chain[DeliverableKind.STRATEGY]
        report = chain[DeliverableKind.PORTFOLIO_REPORT]

    assert plan.based_on_id == strategy.id
    assert strategy.based_on_id == report.id


async def test_saved_strategy_is_readable_afterwards(scope):
    await save_marketing_strategy.ainvoke({"strategy": "ставка на офлайн-выставки"})
    assert "офлайн-выставки" in await get_marketing_strategy.ainvoke({})


async def test_progress_shows_what_is_done(scope):
    await save_portfolio_report.ainvoke({"report": "разбор"})
    progress = await get_artist_progress.ainvoke({})
    assert "✅ Анализ портфолио" in progress
    assert "⬜ Маркетинговая стратегия" in progress


async def test_portfolio_material_warns_when_statement_missing(scope):
    assert "statement не заполнен" in await get_portfolio_material.ainvoke({})


async def test_portfolio_material_includes_profile(scope):
    await update_artist_profile.ainvoke({"statement": "Я работаю с памятью места."})
    assert "памятью места" in await get_portfolio_material.ainvoke({})


# ── open calls ────────────────────────────────────────────────────────
async def test_add_open_call_rejects_an_unparseable_date(scope):
    result = await add_open_call.ainvoke({"title": "Тест", "deadline": "как-нибудь весной"})
    assert "Не смогла разобрать дату" in result


@pytest.mark.parametrize("raw", ["2026-09-15", "15.09.2026", "15/09/2026"])
async def test_add_open_call_accepts_common_date_formats(scope, database, raw):
    await add_open_call.ainvoke({"title": f"Конкурс {raw}", "deadline": raw})
    async with database.session() as session:
        calls = await OpenCallRepository(session).search(today=date(2026, 1, 1))
    assert any(call.deadline == date(2026, 9, 15) for call in calls)


async def test_artist_added_calls_are_trusted(scope, database):
    await add_open_call.ainvoke({"title": "Мой конкурс", "deadline": "2027-01-10"})
    async with database.session() as session:
        calls = await OpenCallRepository(session).search(today=date(2026, 1, 1))
    mine = next(call for call in calls if call.title == "Мой конкурс")
    assert mine.deadline_verified is True
    assert mine.source == "artist"


async def test_seeded_calls_are_rendered_as_estimates(scope, database):
    async with database.session() as session:
        await OpenCallRepository(session).upsert(
            {
                "slug": "estimate",
                "title": "Ежегодная премия",
                "deadline": date.today() + timedelta(days=200),
                "deadline_verified": False,
            }
        )
    result = await search_open_calls.ainvoke({"query": "Ежегодная"})
    assert "ориентировочно" in result
    assert "уточни на сайте" in result


async def test_track_schedules_reminders_and_says_when(scope, database):
    await add_open_call.ainvoke(
        {
            "title": "Резиденция",
            "deadline": (datetime.now(UTC).date() + timedelta(days=90)).isoformat(),
        }
    )
    async with database.session() as session:
        call = await OpenCallRepository(session).get_by_slug(f"artist-{scope.artist_id}-резиденция")
    result = await track_open_call.ainvoke({"open_call_id": call.id})
    assert "Напомню" in result

    async with database.session() as session:
        reminders = await ReminderRepository(session).upcoming_for_artist(scope.artist_id)
    assert len(reminders) == 5


async def test_track_unknown_id_is_handled(scope):
    assert "не найден" in await track_open_call.ainvoke({"open_call_id": 99999})


async def test_untrack_cancels_reminders(scope, database):
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            {
                "slug": "u",
                "title": "U",
                "deadline": datetime.now(UTC).date() + timedelta(days=60),
            }
        )
        call_id = call.id
    await track_open_call.ainvoke({"open_call_id": call_id})
    result = await untrack_open_call.ainvoke({"open_call_id": call_id})
    assert "Больше не слежу" in result

    async with database.session() as session:
        assert await ReminderRepository(session).upcoming_for_artist(scope.artist_id) == []


async def test_tracked_list_is_empty_at_first(scope):
    assert "Пока ничего не отслеживается" in await list_tracked_open_calls.ainvoke({})


async def test_matching_needs_a_profile_first(scope):
    assert "медиум" in await match_open_calls_to_profile.ainvoke({})


async def test_matching_uses_the_stored_profile(scope, database):
    await update_artist_profile.ainvoke({"media": ["видео"], "themes": ["память"]})
    async with database.session() as session:
        await OpenCallRepository(session).upsert(
            {
                "slug": "vid-res",
                "title": "Видеорезиденция",
                "description": "Резиденция для художников, работающих с видео",
                "disciplines": ["видео"],
                "deadline": date.today() + timedelta(days=120),
            }
        )
    result = await match_open_calls_to_profile.ainvoke({})
    assert "Видеорезиденция" in result


# ── knowledge base ────────────────────────────────────────────────────
async def test_knowledge_tool_degrades_gracefully_without_the_extra(scope):
    """Without the [rag] extra the copilot must say so, not crash."""
    result = await search_art_knowledge.ainvoke({"query": "как формируется цена на работы"})
    assert "база знаний" in result.lower() or "базе знаний" in result.lower()
