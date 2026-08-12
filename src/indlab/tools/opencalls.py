"""Open-call monitoring: find them, track them, get reminded.

The honesty rule of this module: seeded open calls are recurring competitions
whose dates move every year, so their deadlines are marked unverified and every
rendering of them carries a "confirm on the website" warning. Calls the artist
added themselves are trusted. A reminder that quietly points at a stale date
would be worse than no reminder at all.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from langchain_core.tools import tool

from indlab.agents.runtime import current_artist
from indlab.config import get_settings
from indlab.db.engine import get_database
from indlab.db.models import OpenCall
from indlab.db.repo import (
    ArtistRepository,
    OpenCallRepository,
    ReminderRepository,
    SubscriptionRepository,
)
from indlab.formatting import plural_days

log = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def format_open_call(call: OpenCall, today: date | None = None, *, detailed: bool = False) -> str:
    """Render one open call for a chat message."""
    today = today or datetime.now(UTC).date()
    lines = [f"#{call.id} · {call.title}"]
    if call.organizer:
        lines.append(f"🏛 {call.organizer}")

    if call.deadline:
        left = call.days_left(today)
        if left is None:
            when = str(call.deadline)
        elif left < 0:
            when = f"{call.deadline} — эта дата уже прошла"
        else:
            when = f"{call.deadline} (осталось {left} {plural_days(left)})"
        if call.deadline_verified:
            lines.append(f"📅 {when}")
        else:
            # Never present a catalogue estimate as a fact.
            lines.append(f"📅 ориентировочно {when} — точную дату уточни на сайте")
    else:
        lines.append("📅 дедлайн уточняй на сайте")

    lines.append(f"📍 {call.where()}")
    if call.fee:
        lines.append(f"💸 взнос: {call.fee}")
    if detailed:
        if call.prize:
            lines.append(f"🏆 {call.prize}")
        if call.eligibility:
            lines.append(f"👤 кто может подать: {call.eligibility}")
        if call.description:
            lines.append(f"\n{call.description}")
    if call.url:
        lines.append(f"🔗 {call.url}")
    return "\n".join(lines)


@tool
async def search_open_calls(
    query: str | None = None,
    disciplines: list[str] | None = None,
    limit: int = 5,
) -> str:
    """Find open calls, residencies and grants the artist could apply to.

    ``query`` matches title, organiser and description; ``disciplines``
    narrows by medium (for example ["живопись", "video"]). Only calls whose
    deadline has not passed are returned. Each result starts with an id — pass
    that id to track_open_call to set up reminders.
    """
    today = datetime.now(UTC).date()
    async with get_database().session() as session:
        calls = await OpenCallRepository(session).search(
            query=query, disciplines=disciplines, limit=max(1, min(limit, 15)), today=today
        )
        rendered = [format_open_call(call, today) for call in calls]

    if not rendered:
        return (
            "По этому запросу ничего не нашлось. Можно смягчить формулировку "
            "или добавить свой опенкол через add_open_call."
        )
    return "\n\n".join(rendered)


@tool
async def add_open_call(
    title: str,
    deadline: str | None = None,
    url: str | None = None,
    location: str | None = None,
    is_online: bool = False,
    organizer: str | None = None,
    description: str | None = None,
    disciplines: list[str] | None = None,
    fee: str | None = None,
) -> str:
    """Save an open call the artist found themselves.

    Use this when the artist pastes a link or describes a competition, grant
    or residency they want to keep an eye on. ``deadline`` must be a date like
    "2026-09-15". The call is stored as verified, because the artist read it
    from the source.
    """
    artist = current_artist()
    parsed = _parse_date(deadline)
    if deadline and parsed is None:
        return (
            f"Не смогла разобрать дату «{deadline}». Нужен формат ГГГГ-ММ-ДД, например 2026-09-15."
        )

    async with get_database().session() as session:
        call = await OpenCallRepository(session).add_custom(
            artist.artist_id,
            {
                "title": title.strip(),
                "deadline": parsed,
                "url": url,
                "location": location,
                "is_online": is_online,
                "organizer": organizer,
                "description": description,
                "disciplines": disciplines or [],
                "fee": fee,
            },
        )
        rendered = format_open_call(call)
        call_id = call.id

    return (
        f"✅ Добавила в твой список:\n\n{rendered}\n\n"
        f"Чтобы я напоминала о дедлайне — скажи, и я включу напоминания (id {call_id})."
    )


@tool
async def track_open_call(open_call_id: int, note: str | None = None) -> str:
    """Start tracking an open call and schedule deadline reminders.

    Reminders are stored in the database, so they survive a restart of the
    bot. The artist gets nudged well before the deadline, not on the day of.
    """
    artist = current_artist()
    settings = get_settings()

    async with get_database().session() as session:
        call = await OpenCallRepository(session).get(open_call_id)
        if call is None:
            return (
                f"Опенкол с id {open_call_id} не найден. Сначала найди его через search_open_calls."
            )

        await SubscriptionRepository(session).subscribe(artist.artist_id, call.id, note)
        reminders = await ReminderRepository(session).schedule_for_call(
            artist.artist_id, call, settings.reminder_lead_days
        )
        title = call.title
        deadline = call.deadline
        verified = call.deadline_verified
        fire_days = sorted((reminder.lead_days for reminder in reminders), reverse=True)

    if deadline is None:
        return (
            f"✅ Слежу за «{title}». Дедлайн не указан — как узнаешь дату, "
            "скажи мне, и я поставлю напоминания."
        )
    if not fire_days:
        return (
            f"✅ Слежу за «{title}» (дедлайн {deadline}), но напоминать уже поздно: "
            "все контрольные точки в прошлом."
        )

    schedule = ", ".join(f"за {days} {plural_days(days)}" for days in fire_days)
    warning = (
        ""
        if verified
        else "\n\n⚠️ Дата взята из каталога и может сдвинуться — проверь её на сайте организатора."
    )
    return f"✅ Слежу за «{title}». Напомню {schedule} до дедлайна {deadline}.{warning}"


@tool
async def untrack_open_call(open_call_id: int) -> str:
    """Stop tracking an open call and cancel its pending reminders."""
    artist = current_artist()
    async with get_database().session() as session:
        removed = await SubscriptionRepository(session).unsubscribe(artist.artist_id, open_call_id)
        cancelled = await ReminderRepository(session).cancel_for_call(
            artist.artist_id, open_call_id
        )
    if not removed:
        return "Этот опенкол и так не отслеживался."
    return f"Больше не слежу за ним. Отменила напоминаний: {cancelled}."


@tool
async def list_tracked_open_calls() -> str:
    """List the open calls the artist is tracking, soonest deadline first."""
    artist = current_artist()
    today = datetime.now(UTC).date()
    async with get_database().session() as session:
        subscriptions = await SubscriptionRepository(session).list_for_artist(artist.artist_id)
        reminders = await ReminderRepository(session).upcoming_for_artist(artist.artist_id)
        next_reminder: dict[int, datetime] = {}
        for reminder in reminders:
            existing = next_reminder.get(reminder.open_call_id)
            if existing is None or reminder.fire_at < existing:
                next_reminder[reminder.open_call_id] = reminder.fire_at

        entries = []
        for subscription in subscriptions:
            call = subscription.open_call
            block = format_open_call(call, today)
            upcoming = next_reminder.get(call.id)
            if upcoming:
                block += f"\n🔔 следующее напоминание: {upcoming.date()}"
            if subscription.note:
                block += f"\n📝 {subscription.note}"
            entries.append((call.deadline or date.max, block))

    if not entries:
        return "Пока ничего не отслеживается. Найди подходящее через search_open_calls."
    entries.sort(key=lambda item: item[0])
    return "\n\n".join(block for _, block in entries)


@tool
async def match_open_calls_to_profile(limit: int = 5) -> str:
    """Find open calls that fit this particular artist's profile.

    Reads the artist's stored media, themes and city, then searches with them.
    Use this when the artist asks "what should I apply to?" rather than
    searching for a specific keyword.
    """
    artist = current_artist()
    today = datetime.now(UTC).date()
    async with get_database().session() as session:
        profile = await ArtistRepository(session).get_profile(artist.artist_id)
        media = [str(item) for item in (profile.media or [])]
        themes = [str(item) for item in (profile.themes or [])]

        if not media and not themes:
            return (
                "Чтобы подобрать опенколы под тебя, мне нужно знать хотя бы медиум "
                "и темы. Спроси художника, в каких техниках он работает."
            )

        repo = OpenCallRepository(session)
        seen: dict[int, OpenCall] = {}
        for term in [*themes, *media, None]:
            for call in await repo.search(
                query=term, disciplines=media or None, limit=limit, today=today
            ):
                seen.setdefault(call.id, call)
            if len(seen) >= limit:
                break
        calls = list(seen.values())[:limit]
        rendered = [format_open_call(call, today) for call in calls]
        criteria = ", ".join(media + themes)

    if not rendered:
        return "Под этот профиль пока ничего не подобралось в каталоге."
    return f"Подобрала под твой профиль ({criteria}):\n\n" + "\n\n".join(rendered)


OPEN_CALL_TOOLS = [
    search_open_calls,
    match_open_calls_to_profile,
    add_open_call,
    track_open_call,
    untrack_open_call,
    list_tracked_open_calls,
]
