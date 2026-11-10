"""Shared pytest fixtures. Later tasks add database fixtures here."""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot_test"


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL),
        jwt_secret="test-secret",
    )


@pytest.fixture
async def client(test_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(test_settings)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
