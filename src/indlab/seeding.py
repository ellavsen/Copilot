"""Load the bundled open-call catalogue into the database.

Recurring competitions move their dates every year, so the catalogue stores a
*typical* month and day rather than a fixed date. At seed time we resolve that
to the next future occurrence and mark it unverified, which is what makes the
bot say "ориентировочно, проверь на сайте" everywhere it shows the date.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

from indlab.config import get_settings
from indlab.db.engine import get_database
from indlab.db.repo import OpenCallRepository

log = logging.getLogger(__name__)


def next_occurrence(month_day: str, today: date | None = None) -> date | None:
    """Resolve ``"MM-DD"`` to the next date on or after ``today``."""
    today = today or datetime.now(UTC).date()
    try:
        month, day = (int(part) for part in month_day.split("-"))
    except (ValueError, TypeError):
        return None
    for year in (today.year, today.year + 1, today.year + 2):
        try:
            candidate = date(year, month, day)
        except ValueError:  # 29 February in a non-leap year
            continue
        if candidate >= today:
            return candidate
    return None


def load_catalogue(path: Path | None = None) -> list[dict]:
    """Read the JSON catalogue and turn it into rows ready for the database."""
    path = path or get_settings().open_calls_seed
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    rows: list[dict] = []
    for entry in payload.get("calls", []):
        entry = dict(entry)
        recurring = entry.pop("recurring_deadline", None)
        typical = entry.pop("typical_period", None)

        description = entry.get("description") or ""
        if typical:
            description = f"{description}\n\n🗓 {typical}".strip()

        rows.append(
            {
                "slug": entry["slug"],
                "title": entry["title"],
                "organizer": entry.get("organizer"),
                "url": entry.get("url"),
                "description": description or None,
                "location": entry.get("location"),
                "is_online": bool(entry.get("is_online", False)),
                "deadline": next_occurrence(recurring) if recurring else None,
                # Catalogue dates are estimates by construction.
                "deadline_verified": False,
                "fee": entry.get("fee"),
                "prize": entry.get("prize"),
                "eligibility": entry.get("eligibility"),
                "disciplines": entry.get("disciplines", []),
                "tags": entry.get("tags", []),
                "source": "seed",
            }
        )
    return rows


async def seed_open_calls(path: Path | None = None) -> int:
    """Insert or refresh the catalogue. Idempotent — safe to run on every boot."""
    rows = load_catalogue(path)
    database = get_database()
    async with database.session() as session:
        repo = OpenCallRepository(session)
        for row in rows:
            await repo.upsert(row)
        total = await repo.count()
    log.info("Seeded %s open calls (catalogue total: %s)", len(rows), total)
    return len(rows)
