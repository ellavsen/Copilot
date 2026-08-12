"""Async engine and session management."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from indlab.config import get_settings
from indlab.db.models import Base

log = logging.getLogger(__name__)


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or get_settings().database_url
        self._ensure_parent_dir()
        self._engine = create_async_engine(self.url, future=True)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    def _ensure_parent_dir(self) -> None:
        prefix = "sqlite+aiosqlite:///"
        if self.url.startswith(prefix) and not self.url.endswith(":memory:"):
            Path(self.url[len(prefix) :]).parent.mkdir(parents=True, exist_ok=True)

    async def create_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("Database ready at %s", self.url)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session, committing on success and rolling back on error."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()


_database: Database | None = None


def get_database() -> Database:
    """Return the process-wide database singleton."""
    global _database
    if _database is None:
        _database = Database()
    return _database


def set_database(database: Database | None) -> None:
    """Override the singleton — used by tests to point at a temp file."""
    global _database
    _database = database
