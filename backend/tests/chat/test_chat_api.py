"""API tests for POST/GET /api/conversations/{id}/messages and thread deletion."""

import uuid

import httpx
from fastapi import FastAPI
from langchain_core.runnables import RunnableConfig

from app.chat import router as chat_router
from tests.conftest import FAKE_REPLY, read_sse, register_user

CONVERSATIONS = "/api/conversations"


async def _new_conversation(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post(CONVERSATIONS, json={}, headers=headers)
    assert r.status_code == 201, r.text
    conversation_id: str = r.json()["id"]
    return conversation_id


def _messages_url(conversation_id: str) -> str:
    return f"{CONVERSATIONS}/{conversation_id}/messages"


async def test_post_message_streams_and_sets_title(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)
    content = "Tell me something interesting about octopuses and their three hearts, please!"

    r = await client.post(_messages_url(cid), json={"content": content}, headers=headers)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = read_sse(r)
    assert events[-1] == ("done", {"content": FAKE_REPLY})
    conversations = (await client.get(CONVERSATIONS, headers=headers)).json()
    assert conversations[0]["title"] == content[:60]
    assert conversations[0]["updated_at"] > conversations[0]["created_at"]


async def test_history_after_one_exchange(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)
    assert (await client.get(_messages_url(cid), headers=headers)).json() == []

    await client.post(_messages_url(cid), json={"content": "  hi there  "}, headers=headers)
    r = await client.get(_messages_url(cid), headers=headers)

    assert r.status_code == 200
    assert r.json() == [
        {"role": "user", "content": "hi there"},
        {"role": "assistant", "content": FAKE_REPLY},
    ]


async def test_post_message_to_other_users_conversation_404(client: httpx.AsyncClient) -> None:
    alice = await register_user(client, email="alice@example.com")
    bob = await register_user(client, email="bob@example.com")
    cid = await _new_conversation(client, alice)

    post = await client.post(_messages_url(cid), json={"content": "hi"}, headers=bob)
    get = await client.get(_messages_url(cid), headers=bob)

    assert post.status_code == 404
    assert get.status_code == 404


async def test_post_empty_message_422(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)

    r = await client.post(_messages_url(cid), json={"content": "   "}, headers=headers)

    assert r.status_code == 422


async def test_concurrent_message_returns_409(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)
    chat_router.in_progress_conversations.add(uuid.UUID(cid))
    try:
        r = await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)
    finally:
        chat_router.in_progress_conversations.discard(uuid.UUID(cid))

    assert r.status_code == 409
    assert r.json() == {"detail": "A reply is already in progress"}


async def test_in_progress_id_released_after_reply(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)

    await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)

    assert uuid.UUID(cid) not in chat_router.in_progress_conversations


async def test_delete_conversation_deletes_thread(app: FastAPI, client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)
    await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)
    config: RunnableConfig = {"configurable": {"thread_id": cid}}
    assert (await app.state.graph.aget_state(config)).values["messages"]

    r = await client.delete(f"{CONVERSATIONS}/{cid}", headers=headers)

    assert r.status_code == 204
    state = await app.state.graph.aget_state(config)
    assert state.values.get("messages", []) == []
