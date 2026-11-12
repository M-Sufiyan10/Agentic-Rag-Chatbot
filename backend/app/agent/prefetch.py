"""Retrieval the server runs before the model answers.

Small local models skip tools they need: a 4B model answers "who won the 2026
World Cup?" from memory, and "tell me about the OneDigital assessment" with "I
can't see your files", without calling ``web_search`` or ``search_documents``.
So the server does the retrieval up front and the model answers with the
results already in front of it:

* **Documents.** When the user has ready documents, their passages are
  searched with the user's message, and passages within
  ``auto_document_max_distance`` of it are kept. Unrelated questions find
  nothing close enough and add nothing.
* **Web.** When ``freshness.needs_fresh_information`` says the message needs
  facts newer than the model's training data, ``web_search`` runs with the
  message as its query, whether or not documents matched: a general
  question can sit close to a technical document ("what is the latest
  version of python?" scores like a question about a security report), and
  that must not stop the search it needs.

Each retrieval is recorded as an ordinary tool call plus its result (an
``AIMessage`` with one ``tool_calls`` entry and a matching ``ToolMessage``), as
if the model had made the call itself. So everything downstream already
handles it: the SSE ``tool_start``/``tool_end``/``citations`` events, the chips
and citations in the transcript, rollback of a failed turn, and the model's
own view of the conversation on later turns. The model keeps its tools and can
still search again with a better query.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.freshness import needs_fresh_information
from app.agent.tools.documents import search_user_documents
from app.config import Settings

logger = logging.getLogger(__name__)

Prefetcher = Callable[[str, RunnableConfig], Awaitable[list[BaseMessage]]]


def _tool_exchange(name: str, query: str, content: str, artifact: Any = None) -> list[BaseMessage]:
    call_id = f"auto_{uuid.uuid4().hex}"
    return [
        AIMessage("", tool_calls=[{"name": name, "args": {"query": query}, "id": call_id}]),
        ToolMessage(content, tool_call_id=call_id, name=name, artifact=artifact),
    ]


def make_prefetcher(
    settings: Settings,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    web_search: BaseTool | None,
) -> Prefetcher:
    """Build the prefetch step for ``build_graph(..., prefetch=...)``.

    ``web_search`` is the agent's ``web_search`` tool, or ``None`` to never
    search the web up front.
    """

    async def search_documents(text: str, configurable: dict[str, Any]) -> list[BaseMessage]:
        documents = configurable.get("documents") or {}
        if not settings.auto_document_search or not documents.get("ready"):
            return []
        try:
            user_id = uuid.UUID(str(configurable.get("user_id")))
        except ValueError:
            return []
        content, citations = await search_user_documents(
            text,
            user_id,
            sessionmaker=sessionmaker,
            embeddings=embeddings,
            settings=settings,
            max_distance=settings.auto_document_max_distance,
        )
        return _tool_exchange("search_documents", text, content, citations) if citations else []

    async def search_web(text: str) -> list[BaseMessage]:
        if (
            web_search is None
            or not settings.auto_web_search
            or not needs_fresh_information(text, cutoff_year=settings.knowledge_cutoff_year)
        ):
            return []
        try:
            result = await web_search.ainvoke({"query": text})
        except Exception:  # the tool reports its own failures as text; this is a safety net
            logger.exception("Automatic web search failed")
            return []
        return _tool_exchange("web_search", text, str(result))

    async def prefetch(text: str, config: RunnableConfig) -> list[BaseMessage]:
        configurable = config.get("configurable") or {}
        return await search_documents(text, configurable) + await search_web(text)

    return prefetch
