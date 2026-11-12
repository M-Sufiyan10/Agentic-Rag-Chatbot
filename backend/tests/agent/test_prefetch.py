"""Server-side retrieval before the model answers: documents by relevance, web by rule."""

import uuid
from typing import Any

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_graph
from app.agent.prefetch import make_prefetcher
from app.auth.models import User
from app.chat.streaming import stream_agent_events
from app.config import Settings
from app.documents.models import Document, DocumentChunk
from tests.fakes import ScriptedChatModel

READY = {"ready": ["notes.txt"], "ready_total": 1, "processing": 0}


@pytest.fixture
def sessionmaker(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


class WebSearchSpy:
    """A stand-in ``web_search`` tool that records its queries."""

    def __init__(self, result: str = "1. Argentina wins\nhttps://example.com\nReport") -> None:
        self.queries: list[str] = []

        @tool
        async def web_search(query: str) -> str:
            """Search the web."""
            self.queries.append(query)
            return result

        self.tool: BaseTool = web_search


async def _user_with_document(db: AsyncSession, embeddings: Embeddings, chunks: list[str]) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    doc = Document(
        user_id=user.id,
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key=f"{user.id}/{uuid.uuid4()}.txt",
        status="ready",
    )
    db.add(doc)
    await db.flush()
    db.add_all(
        DocumentChunk(
            document_id=doc.id,
            user_id=user.id,
            chunk_index=index,
            content=text,
            embedding=embeddings.embed_query(text),
            embedding_model="nomic-embed-text",
        )
        for index, text in enumerate(chunks)
    )
    await db.commit()
    return user


def _config(user_id: str, documents: dict[str, Any] | None = READY) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": "t", "user_id": user_id}
    if documents is not None:
        configurable["documents"] = documents
    return {"configurable": configurable}


def _prefetcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    web: WebSearchSpy | None = None,
    **overrides: Any,
) -> Any:
    return make_prefetcher(
        settings.model_copy(update=overrides),
        sessionmaker=sessionmaker,
        embeddings=embeddings,
        web_search=web.tool if web else None,
    )


def _exchange(messages: list[BaseMessage]) -> tuple[AIMessage, ToolMessage]:
    assert len(messages) == 2
    call, result = messages
    assert isinstance(call, AIMessage) and isinstance(result, ToolMessage)
    assert len(call.tool_calls) == 1
    assert result.tool_call_id == call.tool_calls[0]["id"]
    assert result.name == call.tool_calls[0]["name"]
    return call, result


async def test_relevant_passages_become_a_search_documents_exchange(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    user = await _user_with_document(db_session, embeddings, ["security findings", "unrelated"])
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings)

    messages = await prefetch("security findings", _config(str(user.id)))

    call, result = _exchange(messages)
    assert call.tool_calls[0]["name"] == "search_documents"
    assert call.tool_calls[0]["args"] == {"query": "security findings"}
    # Only the passage within the distance threshold is kept (fake embeddings: identical
    # text has distance 0, different text is far away).
    assert result.text == "[1] notes.txt (chunk 0)\nsecurity findings"
    assert [item["chunk_index"] for item in result.artifact] == [0]


async def test_no_passage_close_enough_adds_nothing(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    user = await _user_with_document(db_session, embeddings, ["security findings"])
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings)

    assert await prefetch("what is harness?", _config(str(user.id))) == []


async def test_documents_are_not_searched_without_ready_documents(
    sessionmaker: async_sessionmaker[AsyncSession], test_settings: Settings
) -> None:
    class Untouchable(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("must not embed")

        def embed_query(self, text: str) -> list[float]:
            raise AssertionError("must not embed")

    prefetch = _prefetcher(test_settings, sessionmaker, Untouchable())
    no_docs = {"ready": [], "ready_total": 0, "processing": 0}

    assert await prefetch("security findings", _config(str(uuid.uuid4()), no_docs)) == []
    assert await prefetch("security findings", _config(str(uuid.uuid4()), None)) == []


async def test_document_search_can_be_switched_off(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    user = await _user_with_document(db_session, embeddings, ["security findings"])
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, auto_document_search=False)

    assert await prefetch("security findings", _config(str(user.id))) == []


async def test_fresh_question_runs_web_search(
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    web = WebSearchSpy()
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, web)

    messages = await prefetch("who won 2026 fifa world cup", _config(str(uuid.uuid4()), None))

    call, result = _exchange(messages)
    assert call.tool_calls[0]["name"] == "web_search"
    assert call.tool_calls[0]["args"] == {"query": "who won 2026 fifa world cup"}
    assert web.queries == ["who won 2026 fifa world cup"]
    assert result.text.startswith("1. Argentina wins")


async def test_ordinary_question_skips_web_search(
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    web = WebSearchSpy()
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, web)

    assert await prefetch("what is harness?", _config(str(uuid.uuid4()), None)) == []
    assert web.queries == []


async def test_web_search_can_be_switched_off(
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    web = WebSearchSpy()
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, web, auto_web_search=False)

    assert await prefetch("latest news", _config(str(uuid.uuid4()), None)) == []
    assert web.queries == []


async def test_fresh_question_searches_the_web_even_when_documents_match(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    # A general question can sit close to a technical document ("what is the latest
    # version of python?" vs a security report), so a document match must not stop the
    # web search the question needs.
    user = await _user_with_document(db_session, embeddings, ["latest findings"])
    web = WebSearchSpy()
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, web)

    messages = await prefetch("latest findings", _config(str(user.id)))

    assert len(messages) == 4
    docs_call, web_call = _exchange(messages[:2])[0], _exchange(messages[2:])[0]
    assert docs_call.tool_calls[0]["name"] == "search_documents"
    assert web_call.tool_calls[0]["name"] == "web_search"
    assert web.queries == ["latest findings"]


async def test_graph_runs_prefetch_once_before_the_model(
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    web = WebSearchSpy()
    model = ScriptedChatModel(replies=[AIMessage("Argentina won.")])
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, web)
    graph = build_graph(model, InMemorySaver(), [web.tool], prefetch=prefetch)

    result = await graph.ainvoke(
        {"messages": [HumanMessage("who won 2026 fifa world cup")]},
        _config(str(uuid.uuid4()), None),
    )

    kinds = [type(m).__name__ for m in result["messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    # The model saw the search result before answering.
    assert isinstance(model.calls[0][-1], ToolMessage)
    assert web.queries == ["who won 2026 fifa world cup"]


async def test_prefetched_search_streams_as_tool_events(
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    web = WebSearchSpy()
    model = ScriptedChatModel(replies=[AIMessage("Argentina won.")])
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings, web)
    graph = build_graph(model, InMemorySaver(), [web.tool], prefetch=prefetch)

    events = [
        (event.event, event.data)
        async for event in stream_agent_events(
            graph, "t", "who won 2026 fifa world cup", str(uuid.uuid4())
        )
    ]

    names = [name for name, _ in events]
    assert names[:2] == ["tool_start", "tool_end"]
    assert names[-1] == "done"
    assert '"web_search"' in str(events[0][1])
    assert '"Argentina won."' in str(events[-1][1])


async def test_prefetched_documents_stream_citations(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    test_settings: Settings,
) -> None:
    user = await _user_with_document(db_session, embeddings, ["security findings"])
    model = ScriptedChatModel(replies=[AIMessage("The findings are [1].")])
    prefetch = _prefetcher(test_settings, sessionmaker, embeddings)
    graph = build_graph(model, InMemorySaver(), [], prefetch=prefetch)

    events = [
        event.event
        async for event in stream_agent_events(graph, "t", "security findings", str(user.id), READY)
    ]

    assert events[0] == "tool_start"
    assert events[1] == "tool_end"
    assert events[-2:] == ["citations", "done"]
