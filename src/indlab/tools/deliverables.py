"""Portfolio report, marketing strategy and promotion plan — really stored.

These three used to be stubs that returned "💾 saved" without writing
anything, which quietly broke the product: the planner's check for an existing
strategy always passed, and nothing survived a restart. Now each one writes a
versioned row and records what it was derived from, so the chain
report → strategy → plan is real and inspectable.
"""

from __future__ import annotations

from langchain_core.tools import tool

from indlab.agents.runtime import current_artist
from indlab.db.engine import get_database
from indlab.db.models import DeliverableKind
from indlab.db.repo import ArtistRepository, DeliverableRepository


@tool
async def get_portfolio_material() -> str:
    """Gather everything known about the artist's practice, ready for analysis.

    Use this at the start of a portfolio analysis. It returns the artist
    statement plus their media, themes, exhibitions and education. If it comes
    back nearly empty, ask the artist for the missing pieces instead of
    inventing them.
    """
    artist = current_artist()
    async with get_database().session() as session:
        profile = await ArtistRepository(session).get_profile(artist.artist_id)
        block = profile.as_prompt_block()
        completeness = profile.completeness()
        has_statement = bool(profile.statement)

    header = f"Материал для анализа (профиль заполнен на {completeness}%):"
    footer = ""
    if not has_statement:
        footer = (
            "\n\n⚠️ Artist statement не заполнен — это основной материал для анализа. "
            "Попроси художника прислать его текст."
        )
    return f"{header}\n{block}{footer}"


@tool
async def save_portfolio_report(
    report: str,
    strengths: list[str] | None = None,
    recommendations: list[str] | None = None,
) -> str:
    """Store a finished portfolio analysis so later steps can build on it.

    Call this only after you have actually written the analysis. ``report`` is
    the full text shown to the artist; ``strengths`` and ``recommendations``
    are short bullet lists used by the strategy step.
    """
    artist = current_artist()
    async with get_database().session() as session:
        deliverable = await DeliverableRepository(session).save(
            artist.artist_id,
            DeliverableKind.PORTFOLIO_REPORT,
            report,
            title="Анализ портфолио",
            data={"strengths": strengths or [], "recommendations": recommendations or []},
        )
        version = deliverable.version
    return (
        f"✅ Анализ портфолио сохранён (версия {version}). "
        "Теперь можно строить маркетинговую стратегию."
    )


@tool
async def get_portfolio_report() -> str:
    """Read the artist's most recent stored portfolio analysis, if any."""
    artist = current_artist()
    async with get_database().session() as session:
        deliverable = await DeliverableRepository(session).current(
            artist.artist_id, DeliverableKind.PORTFOLIO_REPORT
        )
        if deliverable is None:
            return "Анализа портфолио пока нет."
        return f"Анализ портфолио (версия {deliverable.version}):\n{deliverable.content}"


@tool
async def get_marketing_strategy() -> str:
    """Read the artist's current marketing strategy.

    This is a real lookup: if it says there is no strategy, there genuinely is
    none, and a promotion plan must not be written yet.
    """
    artist = current_artist()
    async with get_database().session() as session:
        deliverable = await DeliverableRepository(session).current(
            artist.artist_id, DeliverableKind.STRATEGY
        )
        if deliverable is None:
            return "СТРАТЕГИИ НЕТ. Сначала нужно создать маркетинговую стратегию."
        return f"Маркетинговая стратегия (версия {deliverable.version}):\n{deliverable.content}"


@tool
async def save_marketing_strategy(
    strategy: str,
    target_audience: str | None = None,
    channels: list[str] | None = None,
) -> str:
    """Store a finished marketing strategy, linked to the portfolio analysis."""
    artist = current_artist()
    async with get_database().session() as session:
        repo = DeliverableRepository(session)
        report = await repo.current(artist.artist_id, DeliverableKind.PORTFOLIO_REPORT)
        deliverable = await repo.save(
            artist.artist_id,
            DeliverableKind.STRATEGY,
            strategy,
            title="Маркетинговая стратегия",
            data={"target_audience": target_audience, "channels": channels or []},
            based_on_id=report.id if report else None,
        )
        version = deliverable.version
        linked = report is not None
    suffix = " (опирается на анализ портфолио)" if linked else ""
    return f"✅ Стратегия сохранена (версия {version}){suffix}. Дальше можно составить план."


@tool
async def get_promotion_plan() -> str:
    """Read the artist's current promotion plan."""
    artist = current_artist()
    async with get_database().session() as session:
        deliverable = await DeliverableRepository(session).current(
            artist.artist_id, DeliverableKind.PLAN
        )
        if deliverable is None:
            return "Плана продвижения пока нет."
        return f"План продвижения (версия {deliverable.version}):\n{deliverable.content}"


@tool
async def save_promotion_plan(plan: str, steps: list[str] | None = None) -> str:
    """Store a finished promotion plan, linked to the strategy it came from.

    Refuses to save if no marketing strategy exists yet — a plan without a
    strategy is guesswork.
    """
    artist = current_artist()
    async with get_database().session() as session:
        repo = DeliverableRepository(session)
        strategy = await repo.current(artist.artist_id, DeliverableKind.STRATEGY)
        if strategy is None:
            return (
                "❌ Не сохранено: у художника ещё нет маркетинговой стратегии. "
                "Сначала передай работу MarketingAgent."
            )
        deliverable = await repo.save(
            artist.artist_id,
            DeliverableKind.PLAN,
            plan,
            title="План продвижения",
            data={"steps": steps or []},
            based_on_id=strategy.id,
        )
        version = deliverable.version
    return f"✅ План сохранён (версия {version}) и связан со стратегией."


@tool
async def get_artist_progress() -> str:
    """Show which of the three steps the artist has already completed.

    Useful for orienting yourself at the start of a conversation, and for
    telling the artist what makes sense to do next.
    """
    artist = current_artist()
    labels = {
        DeliverableKind.PORTFOLIO_REPORT: "Анализ портфолио",
        DeliverableKind.STRATEGY: "Маркетинговая стратегия",
        DeliverableKind.PLAN: "План продвижения",
    }
    async with get_database().session() as session:
        chain = await DeliverableRepository(session).latest_chain(artist.artist_id)
        lines = []
        for kind, label in labels.items():
            deliverable = chain.get(kind)
            if deliverable is None:
                lines.append(f"⬜ {label} — не создан")
            else:
                lines.append(
                    f"✅ {label} — версия {deliverable.version}: {deliverable.summary(160)}"
                )
    return "\n".join(lines)


PORTFOLIO_TOOLS = [get_portfolio_material, save_portfolio_report, get_portfolio_report]
STRATEGY_TOOLS = [get_marketing_strategy, save_marketing_strategy, get_portfolio_report]
PLANNER_TOOLS = [get_marketing_strategy, save_promotion_plan, get_promotion_plan]
PROGRESS_TOOLS = [get_artist_progress]
