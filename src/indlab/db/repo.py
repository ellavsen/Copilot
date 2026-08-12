"""Repositories — the only place that knows how data is stored.

Tools and handlers talk to these classes, never to the ORM directly, so the
storage engine can change without touching the agent layer.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from indlab.db.models import (
    Artist,
    ArtistProfile,
    Deliverable,
    DeliverableKind,
    OpenCall,
    OpenCallSubscription,
    Reminder,
)

log = logging.getLogger(__name__)

# Profile columns that hold lists / dicts, where "update" means "merge".
_LIST_FIELDS = {"media", "themes", "goals", "languages"}
_DICT_FIELDS = {"links", "extra"}


class ArtistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> Artist | None:
        result = await self.session.execute(select(Artist).where(Artist.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_id: int, display_name: str | None = None) -> Artist:
        artist = await self.get_by_telegram_id(telegram_id)
        if artist is not None:
            artist.last_seen_at = datetime.now(UTC)
            if display_name and not artist.display_name:
                artist.display_name = display_name
            return artist

        artist = Artist(telegram_id=telegram_id, display_name=display_name)
        artist.profile = ArtistProfile()
        self.session.add(artist)
        await self.session.flush()
        log.info("Registered artist telegram_id=%s", telegram_id)
        return artist

    async def get_profile(self, artist_id: int) -> ArtistProfile:
        result = await self.session.execute(
            select(ArtistProfile).where(ArtistProfile.artist_id == artist_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = ArtistProfile(artist_id=artist_id)
            self.session.add(profile)
            await self.session.flush()
        return profile

    async def update_profile(
        self, artist_id: int, updates: dict[str, Any], *, replace: bool = False
    ) -> ArtistProfile:
        """Apply ``updates`` to the artist's profile.

        Lists and dicts are merged by default so the artist can add one theme
        at a time without wiping the others; pass ``replace=True`` to
        overwrite instead.
        """
        profile = await self.get_profile(artist_id)
        known = {column.key for column in ArtistProfile.__table__.columns}

        for key, value in updates.items():
            if value in (None, "", [], {}):
                continue
            if key not in known:
                # Unknown but potentially useful — keep it rather than drop it.
                profile.extra = {**(profile.extra or {}), key: value}
                continue
            if key in _LIST_FIELDS and not replace:
                incoming = value if isinstance(value, list) else [value]
                current = list(getattr(profile, key) or [])
                for item in incoming:
                    if item not in current:
                        current.append(item)
                setattr(profile, key, current)
            elif key in _DICT_FIELDS and not replace:
                current = dict(getattr(profile, key) or {})
                current.update(value if isinstance(value, dict) else {key: value})
                setattr(profile, key, current)
            else:
                setattr(profile, key, value)

        profile.updated_at = datetime.now(UTC)
        await self.session.flush()
        return profile


class DeliverableRepository:
    """Stores the portfolio report, strategy and plan, with version history."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current(self, artist_id: int, kind: DeliverableKind) -> Deliverable | None:
        result = await self.session.execute(
            select(Deliverable)
            .where(
                Deliverable.artist_id == artist_id,
                Deliverable.kind == kind,
                Deliverable.is_current.is_(True),
            )
            .order_by(Deliverable.version.desc())
        )
        return result.scalars().first()

    async def history(self, artist_id: int, kind: DeliverableKind) -> list[Deliverable]:
        result = await self.session.execute(
            select(Deliverable)
            .where(Deliverable.artist_id == artist_id, Deliverable.kind == kind)
            .order_by(Deliverable.version.desc())
        )
        return list(result.scalars())

    async def save(
        self,
        artist_id: int,
        kind: DeliverableKind,
        content: str,
        *,
        title: str = "",
        data: dict | None = None,
        based_on_id: int | None = None,
    ) -> Deliverable:
        """Store a new version and demote the previous current one."""
        previous = await self.current(artist_id, kind)
        if previous is not None:
            previous.is_current = False

        max_version = await self.session.scalar(
            select(func.coalesce(func.max(Deliverable.version), 0)).where(
                Deliverable.artist_id == artist_id, Deliverable.kind == kind
            )
        )
        deliverable = Deliverable(
            artist_id=artist_id,
            kind=kind,
            version=int(max_version or 0) + 1,
            title=title or kind.value.replace("_", " ").title(),
            content=content,
            data=data or {},
            based_on_id=based_on_id,
            is_current=True,
        )
        self.session.add(deliverable)
        await self.session.flush()
        log.info("Saved %s v%s for artist %s", kind.value, deliverable.version, artist_id)
        return deliverable

    async def latest_chain(self, artist_id: int) -> dict[DeliverableKind, Deliverable | None]:
        """The artist's current report / strategy / plan in one call."""
        return {kind: await self.current(artist_id, kind) for kind in DeliverableKind}


class OpenCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, open_call_id: int) -> OpenCall | None:
        return await self.session.get(OpenCall, open_call_id)

    async def get_by_slug(self, slug: str) -> OpenCall | None:
        result = await self.session.execute(select(OpenCall).where(OpenCall.slug == slug))
        return result.scalar_one_or_none()

    async def upsert(self, payload: dict[str, Any]) -> OpenCall:
        """Insert or refresh a call, keyed by ``slug`` so seeding is idempotent."""
        slug = payload["slug"]
        call = await self.get_by_slug(slug)
        if call is None:
            call = OpenCall(slug=slug)
            self.session.add(call)

        known = {column.key for column in OpenCall.__table__.columns}
        for key, value in payload.items():
            if key in known and key not in {"id", "slug", "created_at", "search_blob"}:
                setattr(call, key, value)
        call.rebuild_search_blob()
        await self.session.flush()
        return call

    async def add_custom(self, artist_id: int, payload: dict[str, Any]) -> OpenCall:
        """An open call the artist found themselves — trusted, so verified."""
        payload = {
            **payload,
            "source": "artist",
            "created_by_artist_id": artist_id,
            "deadline_verified": payload.get("deadline_verified", True),
        }
        payload.setdefault("slug", f"artist-{artist_id}-{payload['title'][:40].strip()}".lower())
        return await self.upsert(payload)

    async def search(
        self,
        *,
        query: str | None = None,
        disciplines: list[str] | None = None,
        only_upcoming: bool = True,
        include_undated: bool = True,
        limit: int = 20,
        today: date | None = None,
    ) -> list[OpenCall]:
        stmt = select(OpenCall)
        if only_upcoming:
            cutoff = today or datetime.now(UTC).date()
            condition = OpenCall.deadline >= cutoff
            if include_undated:
                condition = condition | OpenCall.deadline.is_(None)
            stmt = stmt.where(condition)
        if query:
            # search_blob is already lowercased on write — see OpenCall.
            stmt = stmt.where(OpenCall.search_blob.like(f"%{query.lower()}%"))
        stmt = stmt.order_by(OpenCall.deadline.is_(None), OpenCall.deadline.asc()).limit(limit * 3)

        calls = list((await self.session.execute(stmt)).scalars())

        if disciplines:
            wanted = {d.lower() for d in disciplines}
            calls = [
                call
                for call in calls
                if not call.disciplines
                or wanted & {str(d).lower() for d in call.disciplines}
                or "open" in {str(d).lower() for d in call.disciplines}
            ]
        return calls[:limit]

    async def count(self) -> int:
        return int(await self.session.scalar(select(func.count(OpenCall.id))) or 0)


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, artist_id: int, open_call_id: int) -> OpenCallSubscription | None:
        result = await self.session.execute(
            select(OpenCallSubscription).where(
                OpenCallSubscription.artist_id == artist_id,
                OpenCallSubscription.open_call_id == open_call_id,
            )
        )
        return result.scalar_one_or_none()

    async def subscribe(
        self, artist_id: int, open_call_id: int, note: str | None = None
    ) -> OpenCallSubscription:
        subscription = await self.get(artist_id, open_call_id)
        if subscription is None:
            subscription = OpenCallSubscription(
                artist_id=artist_id, open_call_id=open_call_id, note=note
            )
            self.session.add(subscription)
            await self.session.flush()
        elif note:
            subscription.note = note
        return subscription

    async def unsubscribe(self, artist_id: int, open_call_id: int) -> bool:
        subscription = await self.get(artist_id, open_call_id)
        if subscription is None:
            return False
        await self.session.delete(subscription)
        await self.session.flush()
        return True

    async def list_for_artist(self, artist_id: int) -> list[OpenCallSubscription]:
        result = await self.session.execute(
            select(OpenCallSubscription)
            .where(OpenCallSubscription.artist_id == artist_id)
            .order_by(OpenCallSubscription.created_at.desc())
        )
        return list(result.scalars())


class ReminderRepository:
    """Restart-safe reminders: the schedule lives in the database."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def schedule_for_call(
        self,
        artist_id: int,
        call: OpenCall,
        lead_days: tuple[int, ...] | list[int],
        *,
        at_hour: int = 10,
        now: datetime | None = None,
    ) -> list[Reminder]:
        """Create one reminder per lead time, skipping any already in the past."""
        if call.deadline is None:
            return []

        now = now or datetime.now(UTC)
        created: list[Reminder] = []
        for days in sorted(set(lead_days), reverse=True):
            fire_at = datetime.combine(
                call.deadline - timedelta(days=days), time(hour=at_hour), tzinfo=UTC
            )
            if fire_at <= now:
                continue
            existing = await self.session.execute(
                select(Reminder).where(
                    Reminder.artist_id == artist_id,
                    Reminder.open_call_id == call.id,
                    Reminder.lead_days == days,
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue
            reminder = Reminder(
                artist_id=artist_id,
                open_call_id=call.id,
                lead_days=days,
                fire_at=fire_at,
            )
            self.session.add(reminder)
            created.append(reminder)

        await self.session.flush()
        return created

    async def due(self, now: datetime | None = None, limit: int = 100) -> list[Reminder]:
        now = now or datetime.now(UTC)
        result = await self.session.execute(
            select(Reminder)
            .where(Reminder.sent_at.is_(None), Reminder.fire_at <= now)
            .order_by(Reminder.fire_at.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def mark_sent(self, reminder: Reminder, now: datetime | None = None) -> None:
        reminder.sent_at = now or datetime.now(UTC)
        await self.session.flush()

    async def cancel_for_call(self, artist_id: int, open_call_id: int) -> int:
        result = await self.session.execute(
            select(Reminder).where(
                Reminder.artist_id == artist_id,
                Reminder.open_call_id == open_call_id,
                Reminder.sent_at.is_(None),
            )
        )
        reminders = list(result.scalars())
        for reminder in reminders:
            await self.session.delete(reminder)
        await self.session.flush()
        return len(reminders)

    async def upcoming_for_artist(self, artist_id: int, limit: int = 20) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder)
            .where(Reminder.artist_id == artist_id, Reminder.sent_at.is_(None))
            .order_by(Reminder.fire_at.asc())
            .limit(limit)
        )
        return list(result.scalars())
