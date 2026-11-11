"""Shared pytest fixtures."""

import itertools
import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.main import create_app

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot_test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    """Run ``alembic upgrade head`` once against the test DB.

    Sync on purpose: no event loop is involved, so it cannot clash with the
    function-scoped async fixtures.
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # alembic/env.py reads it via Settings
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(autouse=True)
async def _truncate_users() -> AsyncIterator[None]:
    """Wipe all user data after each test (CASCADE covers dependent tables)."""
    yield
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret="test-secret-test-secret-test-secret-0123",
        upload_dir=str(tmp_path),
    )


FAKE_REPLY = "Hello there"


@pytest.fixture
def chat_model() -> BaseChatModel:
    """A fake chat model that answers every message with ``FAKE_REPLY``.

    Override this fixture in a test module to change what the agent says.
    """
    return GenericFakeChatModel(messages=itertools.repeat(AIMessage(FAKE_REPLY)))


@pytest.fixture
def checkpointer() -> InMemorySaver:
    """An in-memory checkpointer, so API tests don't touch the LangGraph tables."""
    return InMemorySaver()


@pytest.fixture
def tools() -> list[BaseTool]:
    """The agent's tools; none by default. Override in a test module to add some."""
    return []


@pytest.fixture
def embeddings() -> Embeddings:
    """Deterministic fake embeddings; tests never call a real embedding model."""
    return DeterministicFakeEmbedding(size=768)


@pytest.fixture
async def app(
    test_settings: Settings,
    chat_model: BaseChatModel,
    checkpointer: InMemorySaver,
    tools: list[BaseTool],
    embeddings: Embeddings,
) -> AsyncIterator[FastAPI]:
    """The app with its lifespan running, wired to the fake model and checkpointer."""
    application = create_app(
        test_settings,
        chat_model=chat_model,
        checkpointer=checkpointer,
        tools=tools,
        embeddings=embeddings,
    )
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def register_user(
    client: httpx.AsyncClient,
    email: str = "alice@example.com",
    password: str = "correct-horse-1",
) -> dict[str, str]:
    """Register a user through the API and return its ``Authorization`` headers."""
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def read_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    """Parse a buffered SSE response body into ``(event, data)`` pairs.

    Events are separated by a blank line; within an event, ``event:`` names it
    and ``data:`` carries its JSON payload. Comment lines (``: ping``) are skipped.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    body = response.text.replace("\r\n", "\n")
    for block in body.split("\n\n"):
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data += line[len("data:") :].strip()
        if data:
            events.append((name, json.loads(data)))
    return events
