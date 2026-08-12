"""The reminder service — the feature that has to be trustworthy or not exist."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from indlab.db.engine import Database, set_database
from indlab.db.repo import OpenCallRepository, ReminderRepository
from indlab.reminders.service import ReminderService, render_reminder


class Outbox:
    """Stand-in for Telegram."""

    def __init__(self, fail_for: set[int] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_for = fail_for or set()

    async def __call__(self, telegram_id: int, text: str) -> None:
        if telegram_id in self.fail_for:
            raise RuntimeError("chat blocked the bot")
        self.sent.append((telegram_id, text))


async def _make_due_reminder(database, artist, *, verified: bool, days: int = 7):
    now = datetime.now(UTC)
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            {
                "slug": f"call-{verified}-{days}",
                "title": "Резиденция мечты",
                "url": "https://example.org/apply",
                "location": "Лиссабон",
                "deadline": (now + timedelta(days=days)).date(),
                "deadline_verified": verified,
            }
        )
        await ReminderRepository(session).schedule_for_call(
            artist.artist_id, call, (days,), now=now - timedelta(days=1)
        )
    return now


async def test_due_reminder_is_delivered_once(database, artist):
    now = await _make_due_reminder(database, artist, verified=True)
    outbox = Outbox()
    service = ReminderService(outbox, poll_seconds=1)

    delivered = await service.dispatch_due(now + timedelta(days=1))
    assert delivered == 1
    assert outbox.sent[0][0] == artist.telegram_id

    # A second sweep must not resend it.
    assert await service.dispatch_due(now + timedelta(days=2)) == 0
    assert len(outbox.sent) == 1


async def test_nothing_is_sent_before_the_fire_time(database, artist):
    now = datetime.now(UTC)
    async with database.session() as session:
        call = await OpenCallRepository(session).upsert(
            {"slug": "later", "title": "Позже", "deadline": (now + timedelta(days=60)).date()}
        )
        await ReminderRepository(session).schedule_for_call(artist.artist_id, call, (30,), now=now)
    outbox = Outbox()
    assert await ReminderService(outbox).dispatch_due(now) == 0
    assert outbox.sent == []


async def test_a_failed_send_is_retried_next_sweep(database, artist):
    now = await _make_due_reminder(database, artist, verified=True)
    failing = Outbox(fail_for={artist.telegram_id})
    service = ReminderService(failing)

    assert await service.dispatch_due(now + timedelta(days=1)) == 0

    # The reminder was not marked sent, so a healthy sweep still delivers it.
    working = Outbox()
    assert await ReminderService(working).dispatch_due(now + timedelta(days=1)) == 1


async def test_reminders_survive_a_restart(tmp_path):
    """The whole point of storing the schedule in the database."""
    url = "sqlite+aiosqlite:///" + str(tmp_path / "restart.db")

    first = Database(url)
    await first.create_all()
    set_database(first)

    from indlab.db.repo import ArtistRepository

    now = datetime.now(UTC)
    async with first.session() as session:
        artist = await ArtistRepository(session).get_or_create(777, "Восстановленная")
        call = await OpenCallRepository(session).upsert(
            {
                "slug": "persist",
                "title": "Переживёт рестарт",
                "deadline": (now + timedelta(days=7)).date(),
            }
        )
        await ReminderRepository(session).schedule_for_call(
            artist.id, call, (7,), now=now - timedelta(days=1)
        )
    await first.dispose()

    # Simulate a full process restart against the same file.
    second = Database(url)
    set_database(second)
    outbox = Outbox()
    delivered = await ReminderService(outbox).dispatch_due(now + timedelta(days=1))
    await second.dispose()
    set_database(None)

    assert delivered == 1
    assert "Переживёт рестарт" in outbox.sent[0][1]


def test_verified_deadline_reads_as_a_fact():
    text = render_reminder(
        title="Премия",
        deadline="2026-09-15",
        lead_days=7,
        where="онлайн",
        url="https://example.org",
        verified=True,
    )
    assert "Напоминание" in text
    assert "ориентировочная" not in text


def test_unverified_deadline_is_flagged_as_an_estimate():
    """An estimated date must never be presented as a hard deadline."""
    text = render_reminder(
        title="Премия",
        deadline="2026-09-15",
        lead_days=7,
        where="онлайн",
        url="https://example.org",
        verified=False,
    )
    assert "примерно" in text
    assert "ориентировочная" in text
    assert "https://example.org" in text


def test_one_day_reminder_says_tomorrow():
    text = render_reminder("X", "2026-01-02", 1, "онлайн", None, True)
    assert "завтра" in text
