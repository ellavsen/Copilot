"""Deadline reminders.

The schedule lives in the database rather than in an in-process scheduler, so
restarting the bot never loses a reminder — the property that makes the
feature worth trusting. This service simply wakes up, asks for anything due,
sends it, and marks it delivered.

Delivery is injected as a callback, so the whole thing is testable without
Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from indlab.config import get_settings
from indlab.db.engine import get_database
from indlab.db.repo import ReminderRepository
from indlab.formatting import plural_days

log = logging.getLogger(__name__)

SendCallback = Callable[[int, str], Awaitable[None]]


def render_reminder(
    title: str,
    deadline: str,
    lead_days: int,
    where: str,
    url: str | None,
    verified: bool,
) -> str:
    """Compose the nudge the artist receives."""
    when = f"через {lead_days} {plural_days(lead_days)}" if lead_days > 1 else "завтра"
    if verified:
        head = f"🔔 Напоминание: «{title}» — дедлайн {when} ({deadline})."
        tail = ""
    else:
        # The date is our estimate, so the ask is "go check", not "hurry up".
        head = (
            f"🔔 «{title}» — по моим данным приём заявок закрывается примерно {when} ({deadline})."
        )
        tail = (
            "\n\n⚠️ Дата ориентировочная: у таких программ сроки сдвигаются "
            "каждый год. Проверь её на сайте организатора."
        )

    lines = [head, f"📍 {where}"]
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines) + tail


class ReminderService:
    """Polls for due reminders and delivers them."""

    def __init__(
        self,
        send: SendCallback,
        *,
        poll_seconds: int | None = None,
    ) -> None:
        self._send = send
        self._poll_seconds = poll_seconds or get_settings().reminder_poll_seconds
        self._task: asyncio.Task | None = None

    async def dispatch_due(self, now: datetime | None = None) -> int:
        """Send everything that is due. Returns how many were delivered."""
        now = now or datetime.now(UTC)
        delivered = 0

        async with get_database().session() as session:
            repo = ReminderRepository(session)
            due = await repo.due(now)
            for reminder in due:
                call = reminder.open_call
                artist = reminder.artist
                if call is None or artist is None:
                    await repo.mark_sent(reminder, now)
                    continue

                text = render_reminder(
                    title=call.title,
                    deadline=str(call.deadline),
                    lead_days=reminder.lead_days,
                    where=call.where(),
                    url=call.url,
                    verified=call.deadline_verified,
                )
                try:
                    await self._send(artist.telegram_id, text)
                except Exception:
                    log.exception("Could not deliver reminder %s", reminder.id)
                    continue
                await repo.mark_sent(reminder, now)
                delivered += 1

        if delivered:
            log.info("Delivered %s reminder(s)", delivered)
        return delivered

    async def _loop(self) -> None:
        log.info("Reminder service started (every %ss)", self._poll_seconds)
        while True:
            try:
                await self.dispatch_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Reminder sweep failed")
            await asyncio.sleep(self._poll_seconds)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="reminder-service")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            log.info("Reminder service stopped")
