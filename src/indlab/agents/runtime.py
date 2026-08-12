"""Who the agents are currently working for.

The old design passed ``user_id`` as a tool argument and asked the model to
fill it in. That is both fragile (the model forgets) and unsafe (the model
could pass *someone else's* id). Here the identity is bound to the async task
by the Telegram layer before the graph runs, and tools read it from the
context. The model never sees it and cannot change it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtistContext:
    """The artist on whose behalf the current agent run is executing."""

    artist_id: int
    telegram_id: int
    display_name: str | None = None


_current_artist: ContextVar[ArtistContext | None] = ContextVar("current_artist", default=None)


class NoArtistContextError(RuntimeError):
    """Raised when a tool runs outside of an artist scope."""


def current_artist() -> ArtistContext:
    """Return the active artist, or raise if the scope was not set up."""
    context = _current_artist.get()
    if context is None:
        raise NoArtistContextError(
            "No artist bound to this task. Tools must run inside artist_scope()."
        )
    return context


def current_artist_or_none() -> ArtistContext | None:
    return _current_artist.get()


@contextmanager
def artist_scope(context: ArtistContext) -> Iterator[ArtistContext]:
    """Bind ``context`` for the duration of the block."""
    token = _current_artist.set(context)
    try:
        yield context
    finally:
        _current_artist.reset(token)
