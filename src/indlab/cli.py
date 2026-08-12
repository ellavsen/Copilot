"""Console entry points: ``indlab-bot``, ``indlab-ingest``, ``indlab-seed``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from indlab.config import get_settings
from indlab.logging_setup import setup_logging

log = logging.getLogger("indlab.cli")


def run_bot(argv: list[str] | None = None) -> int:
    """Start the Telegram bot."""
    parser = argparse.ArgumentParser(prog="indlab-bot", description="Run the IND-LAB Copilot bot.")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING …")
    args = parser.parse_args(argv)

    setup_logging(args.log_level)
    try:
        from indlab.bot.app import run

        run()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0


def run_seed(argv: list[str] | None = None) -> int:
    """Create the database and load the bundled open-call catalogue."""
    parser = argparse.ArgumentParser(
        prog="indlab-seed", description="Initialise the database and seed open calls."
    )
    parser.add_argument("--catalogue", type=Path, default=None, help="Path to a seed JSON file")
    args = parser.parse_args(argv)

    setup_logging()

    async def main() -> int:
        from indlab.db.engine import get_database
        from indlab.seeding import seed_open_calls

        database = get_database()
        await database.create_all()
        count = await seed_open_calls(args.catalogue)
        await database.dispose()
        print(f"✅ База готова. Загружено опенколов: {count}")
        return 0

    return asyncio.run(main())


def run_ingest(argv: list[str] | None = None) -> int:
    """Build the FAISS index from the local corpus (and optionally Drive)."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="indlab-ingest",
        description="Build the knowledge base index from documents.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=settings.corpus_dir,
        help=f"Folder with PDF/DOCX/MD files (default: {settings.corpus_dir})",
    )
    parser.add_argument(
        "--gdrive-folders",
        default=os.getenv("GDRIVE_FOLDER_IDS", ""),
        help="Comma-separated Google Drive folder ids (optional)",
    )
    parser.add_argument(
        "--gdrive-credentials",
        default=os.getenv("GDRIVE_SERVICE_ACCOUNT_FILE", ""),
        help="Path to a service-account JSON key, kept outside the repository",
    )
    args = parser.parse_args(argv)

    setup_logging()

    from indlab.rag.ingest import MissingDependencyError, ingest

    folder_ids = [part.strip() for part in args.gdrive_folders.split(",") if part.strip()]
    try:
        chunks = ingest(
            corpus_dir=args.corpus,
            gdrive_folder_ids=folder_ids or None,
            gdrive_credentials=args.gdrive_credentials or None,
            settings=settings,
        )
    except MissingDependencyError as exc:
        log.error("%s", exc)
        return 1
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    print(f"✅ Индекс построен: {chunks} фрагментов → {settings.vector_db_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_bot())
