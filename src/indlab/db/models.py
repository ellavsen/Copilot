"""SQLAlchemy 2.0 ORM models — the copilot's long-term memory.

Design notes
------------
*Identity*: an artist is identified by their Telegram id. There are no
passwords anywhere in this project, which is why nothing sensitive can leak
into a chat log.

*Deliverables*: the portfolio report, the marketing strategy and the promotion
plan share one table with a ``kind`` discriminator. They have the same
lifecycle (generated → stored → superseded) and, crucially, they form a chain:
each deliverable can point at the one it was derived from via ``based_on_id``.
That chain is what makes the product coherent — the planner reads the stored
strategy instead of inventing one.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from typing import ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """Timezone-aware ``datetime`` that survives a SQLite round-trip.

    SQLite has no native timestamp type, so values come back naive. We store
    UTC and re-attach the UTC tzinfo on load, so application code never has to
    reason about naive datetimes.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict] = {dict: JSON, list: JSON, datetime: UtcDateTime}


def _now() -> datetime:
    return datetime.now(UTC)


class DeliverableKind(enum.StrEnum):
    """The three artefacts the copilot produces, in dependency order."""

    PORTFOLIO_REPORT = "portfolio_report"
    STRATEGY = "strategy"
    PLAN = "plan"


class CareerStage(enum.StrEnum):
    STUDENT = "student"
    EMERGING = "emerging"
    MID_CAREER = "mid_career"
    ESTABLISHED = "established"


# ─────────────────────────────────────────────────────────────────────
# Artist & profile
# ─────────────────────────────────────────────────────────────────────
class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(8), default="ru")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    profile: Mapped[ArtistProfile] = relationship(
        back_populates="artist", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    deliverables: Mapped[list[Deliverable]] = relationship(
        back_populates="artist", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list[OpenCallSubscription]] = relationship(
        back_populates="artist", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Artist id={self.id} tg={self.telegram_id}>"


class ArtistProfile(Base):
    """Everything the artist tells us about themselves, filled in gradually.

    Every field is optional on purpose: the artist answers what they want,
    when they want, and the copilot works with whatever it has. ``extra``
    absorbs anything that does not deserve its own column yet.
    """

    __tablename__ = "artist_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), unique=True
    )

    full_name: Mapped[str | None] = mapped_column(String(160))
    pronouns: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(96))
    country: Mapped[str | None] = mapped_column(String(96))
    birth_year: Mapped[int | None] = mapped_column(Integer)

    media: Mapped[list] = mapped_column(default=list)
    themes: Mapped[list] = mapped_column(default=list)
    career_stage: Mapped[CareerStage | None] = mapped_column(
        Enum(CareerStage, native_enum=False, length=24)
    )

    statement: Mapped[str | None] = mapped_column(Text)
    education: Mapped[str | None] = mapped_column(Text)
    exhibitions: Mapped[str | None] = mapped_column(Text)
    awards: Mapped[str | None] = mapped_column(Text)
    publications: Mapped[str | None] = mapped_column(Text)

    goals: Mapped[list] = mapped_column(default=list)
    audience: Mapped[str | None] = mapped_column(Text)
    price_range: Mapped[str | None] = mapped_column(String(96))
    languages: Mapped[list] = mapped_column(default=list)
    links: Mapped[dict] = mapped_column(default=dict)
    extra: Mapped[dict] = mapped_column(default=dict)

    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    artist: Mapped[Artist] = relationship(back_populates="profile")

    # Fields that meaningfully improve the copilot's advice, in the order we
    # like to ask for them. Used for the completeness meter and for deciding
    # what to ask next.
    INTERVIEW_FIELDS: ClassVar[tuple[str, ...]] = (
        "full_name",
        "city",
        "media",
        "themes",
        "career_stage",
        "statement",
        "goals",
        "audience",
        "education",
        "exhibitions",
        "price_range",
        "links",
    )

    def filled_fields(self) -> list[str]:
        filled = []
        for name in self.INTERVIEW_FIELDS:
            value = getattr(self, name)
            if value not in (None, "", [], {}):
                filled.append(name)
        return filled

    def missing_fields(self) -> list[str]:
        filled = set(self.filled_fields())
        return [name for name in self.INTERVIEW_FIELDS if name not in filled]

    def completeness(self) -> int:
        """Percentage of interview fields the artist has answered."""
        return round(100 * len(self.filled_fields()) / len(self.INTERVIEW_FIELDS))

    def as_prompt_block(self) -> str:
        """Render the profile as compact context for an LLM prompt."""
        labels = {
            "full_name": "Имя",
            "pronouns": "Местоимения",
            "city": "Город",
            "country": "Страна",
            "birth_year": "Год рождения",
            "media": "Медиум",
            "themes": "Темы",
            "career_stage": "Этап карьеры",
            "statement": "Artist statement",
            "education": "Образование",
            "exhibitions": "Выставки",
            "awards": "Награды",
            "publications": "Публикации",
            "goals": "Цели",
            "audience": "Аудитория",
            "price_range": "Ценовой диапазон",
            "languages": "Языки",
            "links": "Ссылки",
        }
        lines: list[str] = []
        for field, label in labels.items():
            value = getattr(self, field, None)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, list):
                rendered = ", ".join(str(item) for item in value)
            elif isinstance(value, dict):
                rendered = ", ".join(f"{k}: {v}" for k, v in value.items())
            else:
                rendered = str(value)
            lines.append(f"- {label}: {rendered}")
        for key, value in (self.extra or {}).items():
            lines.append(f"- {key}: {value}")
        if not lines:
            return "(профиль пока пустой)"
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Deliverables: report → strategy → plan
# ─────────────────────────────────────────────────────────────────────
class Deliverable(Base):
    __tablename__ = "deliverables"
    __table_args__ = (UniqueConstraint("artist_id", "kind", "version", name="uq_deliverable_ver"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    kind: Mapped[DeliverableKind] = mapped_column(
        Enum(DeliverableKind, native_enum=False, length=32), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(default=dict)
    # Provenance: the strategy that a plan was built from, etc.
    based_on_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    artist: Mapped[Artist] = relationship(back_populates="deliverables")
    based_on: Mapped[Deliverable | None] = relationship(remote_side=[id])

    def summary(self, limit: int = 400) -> str:
        text = " ".join(self.content.split())
        return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ─────────────────────────────────────────────────────────────────────
# Open calls, subscriptions, reminders
# ─────────────────────────────────────────────────────────────────────
class OpenCall(Base):
    __tablename__ = "open_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable key so re-seeding is idempotent.
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    organizer: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    location: Mapped[str | None] = mapped_column(String(200))
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)

    deadline: Mapped[date | None] = mapped_column(Date, index=True)
    # Seeded deadlines are indicative: recurring calls move their dates every
    # year. False means "always tell the artist to confirm on the website".
    deadline_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    fee: Mapped[str | None] = mapped_column(String(120))
    prize: Mapped[str | None] = mapped_column(String(300))
    eligibility: Mapped[str | None] = mapped_column(Text)
    disciplines: Mapped[list] = mapped_column(default=list)
    tags: Mapped[list] = mapped_column(default=list)

    source: Mapped[str] = mapped_column(String(40), default="seed")
    created_by_artist_id: Mapped[int | None] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(default=_now)

    # Lowercased haystack for case-insensitive search, computed in Python.
    #
    # SQLite's built-in lower() is ASCII-only: it leaves "Живопись" untouched,
    # so `lower(title) LIKE '%живопись%'` silently matches nothing — fatal for
    # a catalogue that is entirely in Russian. Normalising on write instead of
    # on read fixes that without depending on any driver behaviour, works the
    # same on PostgreSQL, and is cheaper to query.
    search_blob: Mapped[str] = mapped_column(Text, default="")

    subscriptions: Mapped[list[OpenCallSubscription]] = relationship(
        back_populates="open_call", cascade="all, delete-orphan"
    )

    def rebuild_search_blob(self) -> str:
        """Recompute the lowercased haystack. Call after any field changes."""
        parts = [
            self.title,
            self.organizer,
            self.description,
            self.eligibility,
            self.location,
            " ".join(str(item) for item in (self.disciplines or [])),
            " ".join(str(item) for item in (self.tags or [])),
        ]
        self.search_blob = " ".join(part for part in parts if part).lower()
        return self.search_blob

    def days_left(self, today: date | None = None) -> int | None:
        if self.deadline is None:
            return None
        return (self.deadline - (today or datetime.now(UTC).date())).days

    def where(self) -> str:
        if self.is_online and not self.location:
            return "онлайн"
        if self.is_online and self.location:
            return f"{self.location} / онлайн"
        return self.location or "не указано"


class OpenCallSubscription(Base):
    __tablename__ = "open_call_subscriptions"
    __table_args__ = (UniqueConstraint("artist_id", "open_call_id", name="uq_subscription"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    open_call_id: Mapped[int] = mapped_column(
        ForeignKey("open_calls.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    artist: Mapped[Artist] = relationship(back_populates="subscriptions")
    open_call: Mapped[OpenCall] = relationship(back_populates="subscriptions", lazy="selectin")


class Reminder(Base):
    """A nudge to be delivered at ``fire_at``.

    Reminders live in the database rather than in an in-process scheduler, so
    they survive a restart — the property that makes the feature trustworthy.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("artist_id", "open_call_id", "lead_days", name="uq_reminder"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    open_call_id: Mapped[int] = mapped_column(
        ForeignKey("open_calls.id", ondelete="CASCADE"), index=True
    )
    lead_days: Mapped[int] = mapped_column(Integer)
    fire_at: Mapped[datetime] = mapped_column(index=True)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    open_call: Mapped[OpenCall] = relationship(lazy="selectin")
    artist: Mapped[Artist] = relationship(lazy="selectin")


__all__ = [
    "Artist",
    "ArtistProfile",
    "Base",
    "CareerStage",
    "Deliverable",
    "DeliverableKind",
    "OpenCall",
    "OpenCallSubscription",
    "Reminder",
]
