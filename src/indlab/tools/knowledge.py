"""Retrieval tool over the curated art-theory and art-market corpus."""

from __future__ import annotations

from langchain_core.tools import tool

from indlab.rag.store import get_knowledge_base


@tool
async def search_art_knowledge(query: str, k: int = 4) -> str:
    """Search the curated art-theory and art-market corpus.

    Use this for questions about contemporary art, the art market, pricing,
    galleries, curatorial practice and artist promotion, so the answer rests
    on the corpus rather than on recollection. Every passage comes back with
    its source document — cite those sources in your answer.
    """
    base = get_knowledge_base()
    if not base.available():
        return (
            f"{base.unavailable_reason()} "
            "Отвечай на основе общих знаний и честно предупреди, "
            "что база знаний сейчас не подключена."
        )

    citations = await base.search(query, k=max(1, min(k, 8)))
    if not citations:
        return "В корпусе ничего релевантного не нашлось — отвечай на основе общих знаний."

    blocks = [citation.render() for citation in citations]
    return "Фрагменты из базы знаний:\n\n" + "\n\n".join(blocks)


KNOWLEDGE_TOOLS = [search_art_knowledge]
