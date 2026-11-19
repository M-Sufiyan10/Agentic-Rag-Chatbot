"""API tests for connecting, testing and disconnecting a mailbox."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from cryptography.fernet import Fernet
from fastapi import FastAPI
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.config import Settings
from app.conversations.models import Conversation
from app.email.client import EmailServerError
from app.email.models import EmailAccount, EmailDraft
from app.main import create_app
from tests.conftest import register_user
from tests.email_mcp.fakes import FakeEmailClient

ACCOUNT = "/api/email/account"
BODY: dict[str, Any] = {
    "address": "me@example.com",
    "username": "me",
    "password": "pw-plain",
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_security": "ssl",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_security": "starttls",
}


@pytest.fixture
def fake() -> FakeEmailClient:
    return FakeEmailClient({"check_connection": {"ok": True, "error": None}})


@pytest.fixture
async def email_app(
    test_settings: Settings,
    chat_model: BaseChatModel,
    checkpointer: InMemorySaver,
    embeddings: Embeddings,
    fake: FakeEmailClient,
) -> AsyncIterator[FastAPI]:
    settings = test_settings.model_copy(
        update={
            "email_encryption_key": SecretStr(Fernet.generate_key().decode()),
            "mcp_email_token": SecretStr("tok"),
        }
    )
    application = create_app(
        settings,
        chat_model=chat_model,
        checkpointer=checkpointer,
        tools=[],
        embeddings=embeddings,
        email_client=fake,  # type: ignore[arg-type]
    )
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def ec(email_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=email_app), base_url="http://test"
    ) as c:
        yield c


async def _rows(db_session: AsyncSession) -> list[EmailAccount]:
    return list((await db_session.scalars(select(EmailAccount))).all())


async def test_email_disabled_returns_503(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    r = await client.get(ACCOUNT, headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"] == "Email is not configured on this server."


async def test_put_validates_then_saves_without_returning_password(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers = await register_user(ec)
    r = await ec.put(ACCOUNT, json=BODY, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "password" not in out
    assert out["has_password"] is True
    assert out["address"] == "me@example.com"
    (row,) = await _rows(db_session)
    assert row.password_encrypted != "pw-plain"
    assert [t for t, _ in fake.calls] == ["check_connection"]
    assert fake.calls[0][1]["account"]["password"] == "pw-plain"
    got = await ec.get(ACCOUNT, headers=headers)
    assert got.status_code == 200
    assert "password" not in got.json()


async def test_put_bad_credentials_400_with_reason(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    reason = "IMAP login failed: wrong username or password"
    fake.results["check_connection"] = {"ok": False, "error": reason}
    headers = await register_user(ec)
    r = await ec.put(ACCOUNT, json=BODY, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == reason
    assert await _rows(db_session) == []


async def test_put_email_service_down_502(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    fake.results["check_connection"] = EmailServerError("down")
    headers = await register_user(ec)
    r = await ec.put(ACCOUNT, json=BODY, headers=headers)
    assert r.status_code == 502
    assert r.json()["detail"] == "The email service is unavailable."
    assert await _rows(db_session) == []


async def test_put_without_password_keeps_stored_one(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers = await register_user(ec)
    assert (await ec.put(ACCOUNT, json=BODY, headers=headers)).status_code == 200
    (before,) = await _rows(db_session)
    stored = before.password_encrypted
    body = {k: v for k, v in BODY.items() if k != "password"} | {"imap_host": "imap2.example.com"}
    r = await ec.put(ACCOUNT, json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert fake.calls[-1][1]["account"]["password"] == "pw-plain"
    db_session.expire_all()
    (after,) = await _rows(db_session)
    assert after.imap_host == "imap2.example.com"
    assert after.password_encrypted == stored


async def test_put_without_password_and_no_account_422(ec: httpx.AsyncClient) -> None:
    headers = await register_user(ec)
    body = {k: v for k, v in BODY.items() if k != "password"}
    r = await ec.put(ACCOUNT, json=body, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"] == "A password is required."


async def test_test_endpoint_saves_nothing(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers = await register_user(ec)
    r = await ec.post(f"{ACCOUNT}/test", json=BODY, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None}
    assert await _rows(db_session) == []
    fake.results["check_connection"] = {"ok": False, "error": "nope"}
    r = await ec.post(f"{ACCOUNT}/test", json=BODY, headers=headers)
    assert r.json() == {"ok": False, "error": "nope"}
    body = {k: v for k, v in BODY.items() if k != "password"}
    assert (await ec.post(f"{ACCOUNT}/test", json=body, headers=headers)).status_code == 422


async def test_delete_cancels_pending_drafts(
    ec: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers = await register_user(ec)
    assert (await ec.put(ACCOUNT, json=BODY, headers=headers)).status_code == 200
    user = (await db_session.scalars(select(User))).one()
    convo = Conversation(user_id=user.id)
    db_session.add(convo)
    await db_session.flush()
    import uuid

    pending = EmailDraft(
        user_id=user.id,
        conversation_id=convo.id,
        turn_id=uuid.uuid4(),
        to_address="x@example.com",
        subject="s",
        body="b",
    )
    sent = EmailDraft(
        user_id=user.id,
        conversation_id=convo.id,
        turn_id=uuid.uuid4(),
        to_address="x@example.com",
        subject="s",
        body="b",
        status="sent",
    )
    db_session.add_all([pending, sent])
    await db_session.commit()
    r = await ec.delete(ACCOUNT, headers=headers)
    assert r.status_code == 204
    db_session.expire_all()
    assert await _rows(db_session) == []
    await db_session.refresh(pending)
    await db_session.refresh(sent)
    assert pending.status == "cancelled"
    assert sent.status == "sent"
    assert (await ec.get(ACCOUNT, headers=headers)).status_code == 404


async def test_accounts_are_per_user(ec: httpx.AsyncClient, db_session: AsyncSession) -> None:
    a = await register_user(ec, "a@example.com")
    b = await register_user(ec, "b@example.com")
    assert (await ec.put(ACCOUNT, json=BODY, headers=a)).status_code == 200
    assert (await ec.get(ACCOUNT, headers=b)).status_code == 404
    assert (await ec.delete(ACCOUNT, headers=b)).status_code == 204
    assert (await ec.get(ACCOUNT, headers=a)).status_code == 200
    count = await db_session.scalar(select(func.count()).select_from(EmailAccount))
    assert count == 1


async def test_unreadable_stored_password_is_a_friendly_error(
    ec: httpx.AsyncClient, email_app: FastAPI
) -> None:
    headers = await register_user(ec)
    assert (await ec.put(ACCOUNT, json=BODY, headers=headers)).status_code == 200
    email_app.state.settings.email_encryption_key = SecretStr(Fernet.generate_key().decode())
    body = {k: v for k, v in BODY.items() if k != "password"}
    r = await ec.put(ACCOUNT, json=body, headers=headers)
    assert r.status_code == 409
    assert "enter it again" in r.json()["detail"]


async def test_health_reports_email_status(
    client: httpx.AsyncClient, ec: httpx.AsyncClient, fake: FakeEmailClient
) -> None:
    assert (await client.get("/api/health")).json()["email"] == "off"
    assert (await ec.get("/api/health")).json()["email"] == "ok"
