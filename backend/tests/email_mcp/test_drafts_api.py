"""API tests for listing, sending and cancelling email drafts."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.config import Settings
from app.email.client import EmailServerError
from app.email.crypto import encrypt_password
from app.email.models import EmailAccount, EmailDraft
from app.main import create_app
from tests.conftest import register_user
from tests.email_mcp.fakes import FakeEmailClient

CONVERSATIONS = "/api/conversations"
KEY = SecretStr(Fernet.generate_key().decode())
PASSWORD = "pw-plain-secret"


class SlowEmailClient(FakeEmailClient):
    """Yields to the event loop on every call so concurrent requests really overlap."""

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(0.05)
        return await super().call(tool, arguments)


@pytest.fixture
def max_sends() -> int:
    return 20


@pytest.fixture
def fake() -> FakeEmailClient:
    return SlowEmailClient({"send_email": {"sent": True, "message_id": "<x@y>", "error": None}})


@pytest.fixture
async def email_app(
    test_settings: Settings,
    chat_model: BaseChatModel,
    checkpointer: InMemorySaver,
    embeddings: Embeddings,
    fake: FakeEmailClient,
    max_sends: int,
) -> AsyncIterator[FastAPI]:
    settings = test_settings.model_copy(
        update={
            "email_encryption_key": KEY,
            "mcp_email_token": SecretStr("tok"),
            "email_max_sends_per_hour": max_sends,
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


async def _completed_turn(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[str, str]:
    r = await client.post(CONVERSATIONS, json={}, headers=headers)
    cid: str = r.json()["id"]
    r = await client.post(
        f"{CONVERSATIONS}/{cid}/messages", json={"content": "hello"}, headers=headers
    )
    assert r.status_code == 200
    r = await client.get(f"{CONVERSATIONS}/{cid}/messages", headers=headers)
    turn_id: str = r.json()[1]["id"]
    return cid, turn_id


async def _user_id(db_session: AsyncSession, email: str = "alice@example.com") -> uuid.UUID:
    return (await db_session.scalars(select(User.id).where(User.email == email))).one()


async def _add_account(
    db_session: AsyncSession, user_id: uuid.UUID, password: str = PASSWORD
) -> None:
    db_session.add(
        EmailAccount(
            user_id=user_id,
            address="me@example.com",
            username="me",
            password_encrypted=encrypt_password(password, KEY),
            imap_host="imap.example.com",
            imap_port=993,
            imap_security="ssl",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_security="starttls",
        )
    )
    await db_session.commit()


async def _add_draft(
    db_session: AsyncSession,
    user_id: uuid.UUID,
    cid: str,
    turn_id: str,
    **fields: Any,
) -> uuid.UUID:
    draft = EmailDraft(
        user_id=user_id,
        conversation_id=uuid.UUID(cid),
        turn_id=uuid.UUID(turn_id),
        to_address="bob@example.com",
        subject="Hi",
        body="Hello Bob",
        reply_to_uid="42",
        **fields,
    )
    db_session.add(draft)
    await db_session.commit()
    return draft.id


async def _setup(
    ec: httpx.AsyncClient, db_session: AsyncSession, **fields: Any
) -> tuple[dict[str, str], str, uuid.UUID, uuid.UUID]:
    headers = await register_user(ec)
    cid, turn_id = await _completed_turn(ec, headers)
    uid = await _user_id(db_session)
    await _add_account(db_session, uid)
    did = await _add_draft(db_session, uid, cid, turn_id, **fields)
    return headers, cid, uid, did


def _send(did: uuid.UUID) -> str:
    return f"/api/email/drafts/{did}/send"


async def test_list_only_completed_turns_and_owned(
    ec: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers, cid, uid, did = await _setup(ec, db_session)
    await _add_draft(db_session, uid, cid, str(uuid.uuid4()))  # turn not in graph state
    r = await ec.get(f"{CONVERSATIONS}/{cid}/email-drafts", headers=headers)
    assert r.status_code == 200
    assert [d["id"] for d in r.json()] == [str(did)]
    assert r.json()[0]["status"] == "pending"
    assert r.json()[0]["sent_at"] is None
    other = await register_user(ec, email="bob@example.com")
    r = await ec.get(f"{CONVERSATIONS}/{cid}/email-drafts", headers=other)
    assert r.status_code == 404


async def test_list_works_with_email_disabled(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid, _ = await _completed_turn(client, headers)
    r = await client.get(f"{CONVERSATIONS}/{cid}/email-drafts", headers=headers)
    assert r.status_code == 200
    assert r.json() == []


async def test_send_success(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers, _, _, did = await _setup(ec, db_session)
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert r.json()["sent_at"] is not None
    ((tool, args),) = fake.calls
    assert tool == "send_email"
    assert args["to"] == "bob@example.com"
    assert args["subject"] == "Hi"
    assert args["body"] == "Hello Bob"
    assert args["reply_to_uid"] == "42"
    assert args["account"]["password"] == PASSWORD
    assert args["account"]["address"] == "me@example.com"


async def test_send_failure_marks_failed_with_reason(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    fake.results["send_email"] = {"sent": False, "message_id": None, "error": "Recipient refused"}
    headers, _, _, did = await _setup(ec, db_session)
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert r.json()["error"] == "Recipient refused"
    assert r.json()["sent_at"] is None


async def test_send_email_service_down_marks_failed(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    fake.results["send_email"] = EmailServerError("down")
    headers, _, _, did = await _setup(ec, db_session)
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert r.json()["error"] == "The email service is unavailable."


async def test_send_unreadable_password_marks_failed(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers = await register_user(ec)
    cid, turn_id = await _completed_turn(ec, headers)
    uid = await _user_id(db_session)
    db_session.add(
        EmailAccount(
            user_id=uid,
            address="me@example.com",
            username="me",
            password_encrypted=encrypt_password("x", SecretStr(Fernet.generate_key().decode())),
            imap_host="h",
            imap_port=993,
            imap_security="ssl",
            smtp_host="h",
            smtp_port=587,
            smtp_security="starttls",
        )
    )
    await db_session.commit()
    did = await _add_draft(db_session, uid, cid, turn_id)
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert "re-enter it in Email settings" in r.json()["error"]
    assert fake.calls == []


async def test_send_without_account_404_and_cancels(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers = await register_user(ec)
    cid, turn_id = await _completed_turn(ec, headers)
    did = await _add_draft(db_session, await _user_id(db_session), cid, turn_id)
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 404
    assert fake.calls == []
    db_session.expire_all()
    assert (await db_session.get(EmailDraft, did)).status == "cancelled"  # type: ignore[union-attr]


async def test_send_uncompleted_turn_404(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers, cid, uid, _ = await _setup(ec, db_session)
    did = await _add_draft(db_session, uid, cid, str(uuid.uuid4()))
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 404
    assert fake.calls == []


async def test_double_send_sends_once(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers, _, _, did = await _setup(ec, db_session)
    rs = await asyncio.gather(
        ec.post(_send(did), headers=headers), ec.post(_send(did), headers=headers)
    )
    assert sorted(r.status_code for r in rs) == [200, 409]
    assert [t for t, _ in fake.calls] == ["send_email"]


async def test_send_cancelled_409(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers, _, _, did = await _setup(ec, db_session, status="cancelled")
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"] == "This draft was already sent or cancelled."
    assert fake.calls == []


@pytest.mark.parametrize("max_sends", [1])
async def test_rate_limit_429(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers, cid, uid, did = await _setup(ec, db_session)
    await _add_draft(
        db_session,
        uid,
        cid,
        str(uuid.uuid4()),
        status="sent",
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"] == "Send limit reached: at most 1 emails per hour."
    assert fake.calls == []


@pytest.mark.parametrize("max_sends", [1])
async def test_old_sends_do_not_count(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    headers, cid, uid, did = await _setup(ec, db_session)
    await _add_draft(
        db_session,
        uid,
        cid,
        str(uuid.uuid4()),
        status="sent",
        sent_at=datetime.now(UTC) - timedelta(hours=2),
    )
    r = await ec.post(_send(did), headers=headers)
    assert r.status_code == 200


async def test_cancel_pending(ec: httpx.AsyncClient, db_session: AsyncSession) -> None:
    headers, _, _, did = await _setup(ec, db_session)
    r = await ec.post(f"/api/email/drafts/{did}/cancel", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    r = await ec.post(f"/api/email/drafts/{did}/cancel", headers=headers)
    assert r.status_code == 409


async def test_send_other_users_draft_404(
    ec: httpx.AsyncClient, fake: FakeEmailClient, db_session: AsyncSession
) -> None:
    _, _, _, did = await _setup(ec, db_session)
    other = await register_user(ec, email="bob@example.com")
    assert (await ec.post(_send(did), headers=other)).status_code == 404
    assert (await ec.post(f"/api/email/drafts/{did}/cancel", headers=other)).status_code == 404
    assert fake.calls == []


async def test_send_requires_email_enabled(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    r = await client.post(_send(uuid.uuid4()), headers=headers)
    assert r.status_code == 503
