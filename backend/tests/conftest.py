"""Shared pytest fixtures."""

import os
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
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
def test_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL, jwt_secret="test-secret-test-secret-test-secret-0123"
    )


@pytest.fixture
async def client(test_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(test_settings)
    async with LifespanManager(app):
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
