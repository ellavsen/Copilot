"""Chat-model factory."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from indlab.config import Settings, get_settings


def build_llm(settings: Settings | None = None, **overrides: object) -> ChatOpenAI:
    """Create the chat model used by the supervisor and every agent."""
    settings = settings or get_settings()
    kwargs: dict[str, object] = {
        "model": settings.openai_model,
        "api_key": settings.require_openai_key(),
        "temperature": settings.openai_temperature,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": 2,
    }
    kwargs.update(overrides)
    return ChatOpenAI(**kwargs)  # type: ignore[arg-type]
