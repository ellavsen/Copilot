"""Semantic search over the art-theory corpus.

Three things this fixes compared to the previous version:

1. The embedding model and the FAISS index were built **twice**, once in
   ``utils.py`` and once in ``mirrart_bot.py`` — two copies of a transformer in
   RAM and two cold starts. Here there is exactly one, created lazily on first
   use.
2. Search ran synchronously inside async handlers, freezing the whole bot for
   every user. Here it runs in a worker thread.
3. Results came back as bare text. Here every hit carries its source document,
   so the artist can check where an art-market claim came from.

The heavy dependencies (torch, sentence-transformers, faiss) live in the
optional ``[rag]`` extra. Without them this module reports itself unavailable
and the rest of the product keeps working.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from indlab.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Citation:
    """One retrieved chunk together with where it came from."""

    text: str
    source: str
    path: str = ""
    score: float | None = None

    def render(self, max_chars: int = 700) -> str:
        body = " ".join(self.text.split())
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "…"
        label = self.path or self.source or "корпус"
        return f"{body}\n   [источник: {label}]"


class KnowledgeBase:
    """Lazily-loaded FAISS index. Safe to construct even without the extra."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._store: object | None = None
        self._lock = Lock()
        self._load_failed = False

    @property
    def index_path(self) -> Path:
        return self._settings.vector_db_path

    def index_exists(self) -> bool:
        return (self.index_path / "index.faiss").exists()

    @staticmethod
    def dependencies_installed() -> bool:
        from importlib.util import find_spec

        return all(
            find_spec(name) is not None
            for name in ("faiss", "langchain_community", "langchain_huggingface")
        )

    def available(self) -> bool:
        return not self._load_failed and self.index_exists() and self.dependencies_installed()

    def unavailable_reason(self) -> str:
        if not self.dependencies_installed():
            return (
                "Модуль поиска по базе знаний не установлен. "
                'Поставь его командой: pip install -e ".[rag]"'
            )
        if not self.index_exists():
            return (
                f"База знаний ещё не построена ({self.index_path}). "
                "Собери её командой: indlab-ingest"
            )
        return "База знаний недоступна."

    # ── loading ───────────────────────────────────────────────────────
    def _load_blocking(self) -> object | None:
        """Build the store. Runs once, in a worker thread, under a lock."""
        with self._lock:
            if self._store is not None or self._load_failed:
                return self._store
            try:
                from langchain_community.vectorstores import FAISS
                from langchain_huggingface import HuggingFaceEmbeddings

                log.info("Loading embedding model %s …", self._settings.embedding_model)
                embeddings = HuggingFaceEmbeddings(
                    model_name=self._settings.embedding_model,
                    model_kwargs={"device": self._settings.embedding_device},
                    encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
                )
                # The index is a pickle we generated ourselves from our own
                # corpus; never point this at a file from an untrusted source.
                self._store = FAISS.load_local(
                    str(self.index_path), embeddings, allow_dangerous_deserialization=True
                )
                log.info("Knowledge base loaded from %s", self.index_path)
            except Exception:
                self._load_failed = True
                log.exception("Could not load the knowledge base")
            return self._store

    def _search_blocking(self, query: str, k: int) -> list[Citation]:
        store = self._load_blocking()
        if store is None:
            return []
        hits = store.similarity_search_with_score(query, k=k)
        citations = []
        for document, score in hits:
            metadata = document.metadata or {}
            citations.append(
                Citation(
                    text=document.page_content,
                    source=str(metadata.get("file_name", "")),
                    path=str(metadata.get("path_str", "")),
                    score=float(score),
                )
            )
        return citations

    async def search(self, query: str, k: int | None = None) -> list[Citation]:
        """Retrieve the ``k`` most relevant chunks without blocking the loop."""
        if not self.available():
            return []
        k = k or self._settings.retrieval_top_k
        return await asyncio.to_thread(self._search_blocking, query, k)


_knowledge_base: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    """Return the process-wide knowledge base singleton."""
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase()
    return _knowledge_base


def set_knowledge_base(base: KnowledgeBase | None) -> None:
    """Override the singleton — used by tests."""
    global _knowledge_base
    _knowledge_base = base
