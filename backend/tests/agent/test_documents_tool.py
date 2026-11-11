"""The ``search_documents`` agent tool: per-user scoping, output texts and artifact."""

import uuid
from typing import Any

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_graph
from app.agent.tools.documents import make_search_documents_tool
from app.auth.models import User
from app.config import Settings
from app.documents.models import Document, DocumentChunk
from tests.fakes import ScriptedChatModel, tool_call_reply

NO_DOCUMENTS = "You have no ready documents to search. Upload one from the Documents panel."
STILL_PROCESSING = "Your documents are still being processed. Try again in a moment."
SEARCH_FAILED = "Error: document search failed. Answer without the documents and say so."
UNAVAILABLE = "Error: document search is unavailable in this context."


class FailingEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("ollama down")

    def embed_query(self, text: str) -> list[float]:
        raise ConnectionError("ollama down")


@pytest.fixture
def sessionmaker(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


@pytest.fixture
def search_tool(
    sessionmaker: async_sessionmaker[AsyncSession], embeddings: Embeddings, test_settings: Settings
) -> BaseTool:
    return make_search_documents_tool(
        sessionmaker=sessionmaker, embeddings=embeddings, settings=test_settings
    )


async def _user(db: AsyncSession) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.commit()
    return user


async def _document(
    db: AsyncSession,
    embeddings: Embeddings,
    user: User,
    chunks: list[str],
    *,
    status: str = "ready",
    embedding_model: str = "nomic-embed-text",
    filename: str = "notes.txt",
) -> Document:
    doc = Document(
        user_id=user.id,
        filename=filename,
        content_type="text/plain",
        size_bytes=1,
        storage_key=f"{user.id}/{uuid.uuid4()}.txt",
        status=status,
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
            embedding_model=embedding_model,
        )
        for index, text in enumerate(chunks)
    )
    await db.commit()
    return doc


async def _call(tool: BaseTool, query: str, user_id: str | None) -> ToolMessage:
    configurable: dict[str, Any] = {} if user_id is None else {"user_id": user_id}
    config: RunnableConfig = {"configurable": configurable}
    call = {"name": tool.name, "args": {"query": query}, "id": "c1", "type": "tool_call"}
    message = await tool.ainvoke(call, config)
    assert isinstance(message, ToolMessage)
    return message


def test_schema_exposes_only_query(search_tool: BaseTool) -> None:
    assert search_tool.name == "search_documents"
    assert set(search_tool.tool_call_schema.model_json_schema()["properties"]) == {"query"}


async def test_model_supplied_user_id_is_ignored(
    db_session: AsyncSession, embeddings: Embeddings, search_tool: BaseTool
) -> None:
    alice, bob = await _user(db_session), await _user(db_session)
    await _document(db_session, embeddings, alice, ["zebra migration"])
    bob_doc = await _document(db_session, embeddings, bob, ["quantum tunnelling"])
    model = ScriptedChatModel(
        replies=[
            tool_call_reply(
                "search_documents", {"query": "zebra migration", "user_id": str(alice.id)}, "c1"
            ),
            AIMessage("done"),
        ]
    )
    graph = build_graph(model, InMemorySaver(), [search_tool])
    config: RunnableConfig = {"configurable": {"thread_id": "t1", "user_id": str(bob.id)}}

    result = await graph.ainvoke({"messages": [HumanMessage("find zebras")]}, config)

    (tool_message,) = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "zebra" not in tool_message.text
    assert "quantum tunnelling" in tool_message.text
    assert [item["document_id"] for item in tool_message.artifact] == [str(bob_doc.id)]


async def test_results_and_artifact_shape(
    db_session: AsyncSession, embeddings: Embeddings, search_tool: BaseTool
) -> None:
    alice = await _user(db_session)
    long_text = "word \n\n  " * 200
    doc = await _document(
        db_session, embeddings, alice, ["zebra migration", long_text], filename="zoo.md"
    )

    message = await _call(search_tool, "zebra migration", str(alice.id))

    assert message.text.startswith(
        "[1] zoo.md (chunk 0)\nzebra migration\n\n[2] zoo.md (chunk 1)\n"
    )
    assert message.artifact[0] == {
        "document_id": str(doc.id),
        "filename": "zoo.md",
        "chunk_index": 0,
        "snippet": "zebra migration",
    }
    snippet = message.artifact[1]["snippet"]
    assert len(snippet) <= 300
    assert snippet.startswith("word word ")
    assert "\n" not in snippet and "  " not in snippet
    assert set(message.artifact[1]) == {"document_id", "filename", "chunk_index", "snippet"}


async def test_no_documents_text(db_session: AsyncSession, search_tool: BaseTool) -> None:
    alice = await _user(db_session)

    message = await _call(search_tool, "anything", str(alice.id))

    assert message.text == NO_DOCUMENTS
    assert message.artifact == []


async def test_still_processing_text(
    db_session: AsyncSession, embeddings: Embeddings, search_tool: BaseTool
) -> None:
    alice = await _user(db_session)
    await _document(db_session, embeddings, alice, [], status="processing")

    message = await _call(search_tool, "anything", str(alice.id))

    assert message.text == STILL_PROCESSING
    assert message.artifact == []


async def test_mismatch_note_is_appended(
    db_session: AsyncSession, embeddings: Embeddings, search_tool: BaseTool
) -> None:
    alice = await _user(db_session)
    await _document(db_session, embeddings, alice, ["zebra migration"])
    await _document(db_session, embeddings, alice, ["a", "b"], embedding_model="other")

    message = await _call(search_tool, "zebra migration", str(alice.id))

    assert message.text.startswith("[1] notes.txt (chunk 0)\nzebra migration")
    assert message.text.endswith(
        "\n\nNote: 2 chunks were indexed with a different embedding model and were skipped; "
        "ask the administrator to run the reindex command."
    )
    assert len(message.artifact) == 1


async def test_embeddings_failure_is_reported(
    db_session: AsyncSession,
    embeddings: Embeddings,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    alice = await _user(db_session)
    await _document(db_session, embeddings, alice, ["zebra migration"])
    tool = make_search_documents_tool(
        sessionmaker=sessionmaker, embeddings=FailingEmbeddings(), settings=test_settings
    )

    message = await _call(tool, "zebra migration", str(alice.id))

    assert message.text == SEARCH_FAILED
    assert message.artifact == []


async def test_missing_user_id_is_unavailable(search_tool: BaseTool) -> None:
    message = await _call(search_tool, "anything", None)

    assert message.text == UNAVAILABLE
    assert message.artifact == []
