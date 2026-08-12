"""Logging configuration.

The previous version called ``basicConfig`` twice — the first call, at DEBUG,
won — so production logs contained full prompts, raw model output and the text
of artists' CVs. Personal data does not belong in a log file. Here the default
is INFO, the chatty HTTP and model libraries are pinned to WARNING, and DEBUG
is something you opt into with LOG_LEVEL.
"""

from __future__ import annotations

import logging

from indlab.config import get_settings

# These log request bodies and completions at DEBUG.
NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
    "telegram.ext.Application",
    "telegram.ext.ExtBot",
    "sentence_transformers",
    "transformers",
    "langchain",
    "langgraph",
    "aiosqlite",
    "sqlalchemy.engine",
)


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger exactly once."""
    settings = get_settings()
    resolved = (level or settings.log_level).upper()

    logging.basicConfig(
        level=resolved,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        force=True,
    )
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    if resolved == "DEBUG":
        logging.getLogger("indlab").warning(
            "LOG_LEVEL=DEBUG: prompts and model output will be written to the log. "
            "Do not use this on a machine that handles real artists' data."
        )
