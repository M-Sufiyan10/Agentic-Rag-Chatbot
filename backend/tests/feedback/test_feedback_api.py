"""API tests for thumbs feedback and the turn ids/feedback in the transcript."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.transcript import completed_turn_ids
from app.feedback.models import MessageFeedback
from tests.chat.test_streaming import FailingChatModel
from tests.conftest import register_user
from tests.fakes import FakeTracing

CONVERSATIONS = "/api/conversations"


async def _completed_turn(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[str, str]:
    """Create a conversation, complete one turn, return (conversation id, turn id)."""
    r = await client.post(CONVERSATIONS, json={}, headers=headers)
    cid: str = r.json()["id"]
    r = await client.post(
        f"{CONVERSATIONS}/{cid}/messages", json={"content": "hello"}, headers=headers
    )
    assert r.status_code == 200
    transcript = await _transcript(client, headers, cid)
    turn_id: str = transcript[1]["id"]
    return cid, turn_id


async def _transcript(
    client: httpx.AsyncClient, headers: dict[str, str], cid: str
) -> list[dict[str, Any]]:
    r = await client.get(f"{CONVERSATIONS}/{cid}/messages", headers=headers)
    assert r.status_code == 200
    body: list[dict[str, Any]] = r.json()
    return body


def _url(cid: str, turn_id: str) -> str:
    return f"{CONVERSATIONS}/{cid}/messages/{turn_id}/feedback"


async def _row_count(db_session: AsyncSession) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(MessageFeedback)) or 0)


async def test_transcript_has_turn_ids_and_no_feedback_yet(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    user_msg, assistant = await _transcript(client, headers, cid)
    assert user_msg["id"] is None
    assert str(uuid.UUID(turn_id)) == turn_id
    assert assistant["feedback"] is None


@pytest.mark.parametrize("chat_model", [FailingChatModel(messages=iter([]))])
async def test_transcript_ids_only_on_completed_turns(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    r = await client.post(CONVERSATIONS, json={}, headers=headers)
    cid = r.json()["id"]
    await client.post(f"{CONVERSATIONS}/{cid}/messages", json={"content": "x"}, headers=headers)
    assert await _transcript(client, headers, cid) == []


def test_completed_turn_ids_skips_turns_ending_in_a_tool_call() -> None:
    done = [HumanMessage("a", id="t1"), AIMessage("answer")]
    pending = [
        HumanMessage("b", id="t2"),
        AIMessage("", tool_calls=[{"name": "echo", "args": {}, "id": "c1"}]),
    ]
    assert completed_turn_ids(done + pending) == {"t1"}


async def test_put_then_get_shows_feedback(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    r = await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    assert (r.status_code, r.json()) == (200, {"value": "up"})
    assert (await _transcript(client, headers, cid))[1]["feedback"] == "up"
    await client.put(_url(cid, turn_id), json={"value": "down"}, headers=headers)
    assert (await _transcript(client, headers, cid))[1]["feedback"] == "down"


async def test_delete_clears_feedback(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    assert (await client.delete(_url(cid, turn_id), headers=headers)).status_code == 204
    assert (await _transcript(client, headers, cid))[1]["feedback"] is None
    assert (await client.delete(_url(cid, turn_id), headers=headers)).status_code == 204


async def test_feedback_404s(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    alice = await register_user(client, email="alice@example.com")
    bob = await register_user(client, email="bob@example.com")
    cid, turn_id = await _completed_turn(client, alice)
    body = {"value": "up"}
    assert (await client.put(_url(cid, turn_id), json=body, headers=bob)).status_code == 404
    assert (await client.delete(_url(cid, turn_id), headers=bob)).status_code == 404
    random_turn = str(uuid.uuid4())
    assert (await client.put(_url(cid, random_turn), json=body, headers=alice)).status_code == 404
    assert (await client.delete(_url(cid, random_turn), headers=alice)).status_code == 404
    assert (await client.put(_url(cid, "nope"), json=body, headers=alice)).status_code == 422
    missing = str(uuid.uuid4())
    assert (await client.put(_url(missing, turn_id), json=body, headers=alice)).status_code == 404
    assert await _row_count(db_session) == 0


async def test_feedback_records_score(app: FastAPI, client: httpx.AsyncClient) -> None:
    fake = FakeTracing()
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    app.state.tracing = fake
    await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    assert len(fake.recorded) == 1
    call = fake.recorded[0]
    assert (call["turn_id"], call["positive"]) == (turn_id, True)
    assert call["first_voted_at"].tzinfo is not None
    await client.delete(_url(cid, turn_id), headers=headers)
    assert fake.deleted == [turn_id]


async def test_changing_vote_reuses_first_timestamp(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    fake = FakeTracing()
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    app.state.tracing = fake
    await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    await client.put(_url(cid, turn_id), json={"value": "down"}, headers=headers)
    first, second = fake.recorded
    assert first["first_voted_at"] == second["first_voted_at"]
    assert second["positive"] is False


async def test_feedback_saved_without_tracing(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    app.state.tracing = None
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    r = await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    assert r.status_code == 200
    assert await _row_count(db_session) == 1


async def test_deleting_conversation_deletes_feedback(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    assert (await client.delete(f"{CONVERSATIONS}/{cid}", headers=headers)).status_code == 204
    assert await _row_count(db_session) == 0


async def test_concurrent_first_votes_leave_one_row(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    results = await asyncio.gather(
        client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers),
        client.put(_url(cid, turn_id), json={"value": "down"}, headers=headers),
    )
    assert [r.status_code for r in results] == [200, 200]
    assert await _row_count(db_session) == 1


async def test_vote_change_advances_updated_at_and_keeps_created_at(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers = await register_user(client)
    cid, turn_id = await _completed_turn(client, headers)
    await client.put(_url(cid, turn_id), json={"value": "up"}, headers=headers)
    first = (await db_session.execute(select(MessageFeedback))).scalar_one()
    created, updated = first.created_at, first.updated_at
    await db_session.rollback()  # end the read transaction so the next read sees the change
    await client.put(_url(cid, turn_id), json={"value": "down"}, headers=headers)
    row = (await db_session.execute(select(MessageFeedback))).scalar_one()
    await db_session.refresh(row)
    assert row.created_at == created
    assert row.updated_at > updated
    assert row.positive is False
