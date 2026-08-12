"""Shared fixtures.

Every test runs against a throwaway SQLite file and never touches the network,
so the whole suite works without an OpenAI key and without the heavy ``[rag]``
extra installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

from indlab.agents.runtime import ArtistContext, artist_scope
from indlab.db.engine import Database, set_database
from indlab.db.repo import ArtistRepository


@pytest.fixture
async def database(tmp_path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.create_all()
    set_database(db)
    try:
        yield db
    finally:
        await db.dispose()
        set_database(None)


@pytest.fixture
async def artist(database: Database) -> ArtistContext:
    async with database.session() as session:
        record = await ArtistRepository(session).get_or_create(
            telegram_id=424242, display_name="Тестовая Художница"
        )
        return ArtistContext(
            artist_id=record.id,
            telegram_id=record.telegram_id,
            display_name=record.display_name,
        )


@pytest.fixture
def scope(artist: ArtistContext) -> Iterator[ArtistContext]:
    """Run the test body as if the bot had bound this artist."""
    with artist_scope(artist):
        yield artist


@pytest.fixture
async def second_artist(database: Database) -> ArtistContext:
    async with database.session() as session:
        record = await ArtistRepository(session).get_or_create(
            telegram_id=999, display_name="Другая"
        )
        return ArtistContext(artist_id=record.id, telegram_id=record.telegram_id)
