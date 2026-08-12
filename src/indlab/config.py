"""Application settings, loaded from environment / .env.

Nothing in this module raises at import time: tests and the static tooling can
import it without any secret being present. Credentials are validated only at
the moment they are actually needed (see :func:`Settings.require_bot_token`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Runtime configuration for the copilot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Credentials ───────────────────────────────────────────────────
    bot_token: str | None = Field(default=None, description="Telegram bot token from @BotFather")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")

    # ── Language model ────────────────────────────────────────────────
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.4
    llm_timeout_seconds: int = 90

    # ── Storage ───────────────────────────────────────────────────────
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'indlab.db'}"
    checkpoint_path: Path = PROJECT_ROOT / "data" / "checkpoints.sqlite"

    # ── Retrieval ─────────────────────────────────────────────────────
    vector_db_path: Path = PROJECT_ROOT / "data" / "vector_db"
    corpus_dir: Path = PROJECT_ROOT / "data" / "corpus"
    embedding_model: str = "deepvk/USER-bge-m3"
    embedding_device: str = "cpu"
    retrieval_top_k: int = 4
    chunk_size: int = 1000
    chunk_overlap: int = 150

    # ── Open calls & reminders ────────────────────────────────────────
    open_calls_seed: Path = PACKAGE_ROOT / "data" / "open_calls.seed.json"
    # How many days before a deadline the artist gets nudged.
    reminder_lead_days: tuple[int, ...] = (30, 14, 7, 3, 1)
    reminder_poll_seconds: int = 900
    default_timezone: str = "Europe/Moscow"

    # ── Observability ─────────────────────────────────────────────────
    log_level: str = "INFO"
    # LangSmith tracing is opt-in; see docs/architecture.md.
    langsmith_tracing: bool = False

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("reminder_lead_days", mode="before")
    @classmethod
    def _parse_lead_days(cls, value: object) -> object:
        """Accept ``REMINDER_LEAD_DAYS=30,14,7`` from the environment."""
        if isinstance(value, str):
            return tuple(int(part) for part in value.replace(" ", "").split(",") if part)
        return value

    # ── Guarded accessors ─────────────────────────────────────────────
    def require_bot_token(self) -> str:
        if not self.bot_token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Copy .env.example to .env and add your "
                "token from @BotFather."
            )
        return self.bot_token

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        return self.openai_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
