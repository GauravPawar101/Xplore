"""
LangChain building blocks for Xplore:
  - CodebaseRetriever  — hybrid keyword+vector search (wraps existing Milvus+Postgres)
  - build_chat_chain() — streaming chat with automatic per-session history management

Text generation uses Ollama (configured via OLLAMA_HOST / OLLAMA_MODEL in config.py).
"""

import asyncio
import logging
import os
from typing import Any, Optional

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_ollama import ChatOllama
from pydantic import Field

from shared.config import OLLAMA_HOST, OLLAMA_MODEL

log = logging.getLogger("xplore.rag_chain")

# ─── Retriever ────────────────────────────────────────────────────────────────


class CodebaseRetriever(BaseRetriever):
    """
    Hybrid code-search retriever.

    Runs a keyword search against Postgres (via db.rag_query_keyword) and an
    optional ANN vector search against Milvus (via embedding.embed_text +
    milvus_service.search), then deduplicates and returns LangChain Documents.
    """

    codebase_id: str
    k: int = Field(default=8)
    use_vector: bool = Field(default=True)
    program_id: Optional[str] = Field(default=None)

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: AsyncCallbackManagerForRetrieverRun,
    ) -> list[Document]:
        # Import here to avoid circular imports at module load time
        from shared import db, milvus_service
        from shared.embedding import embed_text

        chunks: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        # Keyword search
        try:
            kw_results = await db.rag_query_keyword(
                codebase_id=self.codebase_id,
                query=query,
                k=self.k,
                program_id=self.program_id,
            )
            for c in kw_results:
                cid = c.get("id", "")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    chunks.append(c)
        except Exception as exc:
            log.debug("Keyword RAG failed: %s", exc)

        # Vector search (optional)
        if self.use_vector and milvus_service.is_available() and len(chunks) < self.k:
            try:
                vec = await embed_text(query)
                if vec:
                    hits = milvus_service.search(
                        self.codebase_id,
                        vec,
                        k=self.k - len(chunks),
                    )
                    if hits:
                        symbol_ids = [h["symbol_id"] for h in hits if h.get("symbol_id")]
                        if symbol_ids:
                            vector_chunks = await db.get_graph_nodes_by_ids(
                                self.codebase_id, symbol_ids
                            )
                            for c in vector_chunks:
                                cid = c.get("id", "")
                                if cid and cid not in seen_ids:
                                    seen_ids.add(cid)
                                    chunks.append(c)
            except Exception as exc:
                log.debug("Vector RAG failed: %s", exc)

        return [_chunk_to_document(c) for c in chunks[: self.k]]

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        """Sync fallback — runs the async implementation in an event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already inside an event loop (FastAPI/uvicorn); create a task
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._aget_relevant_documents(query, run_manager=None))  # type: ignore[arg-type]
                return future.result()
        return asyncio.run(self._aget_relevant_documents(query, run_manager=None))  # type: ignore[arg-type]


def _chunk_to_document(chunk: dict[str, Any]) -> Document:
    code = chunk.get("code") or chunk.get("content") or ""
    return Document(
        page_content=code,
        metadata={
            "id": chunk.get("id", ""),
            "type": chunk.get("type", "symbol"),
            "label": chunk.get("name", ""),
            "filepath": chunk.get("filepath", ""),
            "summary": chunk.get("summary", ""),
        },
    )


# ─── Chat chain with history ──────────────────────────────────────────────────

_CHAT_SYSTEM = (
    "You are a helpful assistant for developers using Xplore. "
    "Answer concisely and accurately. You can discuss code, architecture, and "
    "the codebase. Use Markdown when useful."
)

_CHAT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _CHAT_SYSTEM),
        MessagesPlaceholder("history"),
        ("human", "{input}"),
    ]
)

# LRU-bounded session history store: evicts oldest sessions beyond MAX_SESSIONS.
_MAX_SESSIONS = int(os.environ.get("XPLORE_MAX_CHAT_SESSIONS", "100"))


class _LRUSessionStore:
    """Thread-safe OrderedDict-based LRU cache for chat session histories."""

    def __init__(self, maxsize: int) -> None:
        from collections import OrderedDict
        self._maxsize = maxsize
        self._data: "OrderedDict[str, InMemoryChatMessageHistory]" = OrderedDict()

    def get(self, session_id: str) -> InMemoryChatMessageHistory:
        if session_id not in self._data:
            self._data[session_id] = InMemoryChatMessageHistory()
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
        else:
            self._data.move_to_end(session_id)
        return self._data[session_id]

    def __len__(self) -> int:
        return len(self._data)


_SESSION_STORE = _LRUSessionStore(_MAX_SESSIONS)


def _get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    return _SESSION_STORE.get(session_id)


# Cached ChatOllama instance — stateless, safe to reuse across connections.
_CHAT_OLLAMA: "ChatOllama | None" = None


def _get_chat_ollama() -> ChatOllama:
    global _CHAT_OLLAMA
    if _CHAT_OLLAMA is None:
        _CHAT_OLLAMA = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, streaming=True)
    return _CHAT_OLLAMA


def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    """Public accessor for the per-session LangChain message history object."""
    return _SESSION_STORE.get(session_id)


def build_chat_chain() -> RunnableWithMessageHistory:
    """
    Returns a streaming chat chain with per-session LRU-evicted history.

    Usage:
        chain = build_chat_chain()
        async for chunk in chain.astream(
            {"input": user_text},
            config={"configurable": {"session_id": session_id}},
        ):
            yield chunk
    """
    base_chain = _CHAT_PROMPT | _get_chat_ollama() | StrOutputParser()
    return RunnableWithMessageHistory(
        base_chain,
        _get_session_history,
        input_messages_key="input",
        history_messages_key="history",
    )
