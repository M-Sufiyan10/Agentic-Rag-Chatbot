"""API tests for POST/GET /api/conversations/{id}/messages and thread deletion."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.chat import deps as chat_deps
from app.chat import router as chat_router
from tests.conftest import FAKE_REPLY, read_sse, register_user
from tests.fakes import ScriptedChatModel, echo, tool_call_reply

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
        {"role": "user", "content": "hi there", "tools": []},
        {"role": "assistant", "content": FAKE_REPLY, "tools": []},
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
    chat_deps.in_progress_conversations.add(uuid.UUID(cid))
    try:
        r = await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)
    finally:
        chat_deps.in_progress_conversations.discard(uuid.UUID(cid))

    assert r.status_code == 409
    assert r.json() == {"detail": "A reply is already in progress"}


async def test_in_progress_id_released_after_reply(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)

    await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)

    assert uuid.UUID(cid) not in chat_deps.in_progress_conversations


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


class FailsOnceChatModel(GenericFakeChatModel):
    """A fake model whose first call fails (server down), later calls succeed."""

    failures_left: int = 1

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise ConnectionError("model server unreachable")
        async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
            yield chunk


# Parametrizing a fixture's name overrides it for this one test only.
@pytest.mark.parametrize("chat_model", [FailsOnceChatModel(messages=iter([AIMessage("ok")]))])
async def test_retry_after_model_failure_leaves_clean_history(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)

    first = await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)
    assert [name for name, _ in read_sse(first)][-1] == "error"
    retry = await client.post(_messages_url(cid), json={"content": "hi"}, headers=headers)
    assert read_sse(retry)[-1] == ("done", {"content": "ok"})

    history = await client.get(_messages_url(cid), headers=headers)
    assert history.json() == [
        {"role": "user", "content": "hi", "tools": []},
        {"role": "assistant", "content": "ok", "tools": []},
    ]


async def test_disconnect_rolls_back_before_releasing_id() -> None:
    # sse-starlette closes the router's generator on disconnect. The rollback of the
    # incomplete turn must be done by the time that close returns, i.e. before the
    # conversation is released for a new message (no extra event-loop tick here).
    model = ScriptedChatModel(replies=[tool_call_reply("echo", {"text": "hi"}, "c1")])
    graph = build_graph(model, InMemorySaver(), tools=[echo])
    conversation_id = uuid.uuid4()
    chat_deps.in_progress_conversations.add(conversation_id)

    agen = chat_router.stream_and_release(graph, conversation_id, "x")
    async for event in agen:
        if event.event == "tool_end":
            break
    await agen.aclose()

    config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
    assert (await graph.aget_state(config)).values.get("messages", []) == []
    assert conversation_id not in chat_deps.in_progress_conversations


@pytest.mark.parametrize(
    "chat_model",
    [
        ScriptedChatModel(
            replies=[tool_call_reply("echo", {"text": "hi"}, "c1"), AIMessage("It said hi")]
        )
    ],
)
@pytest.mark.parametrize("tools", [[echo]])
async def test_tool_events_streamed_and_listed_in_transcript(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)

    r = await client.post(_messages_url(cid), json={"content": "say hi"}, headers=headers)

    names = [name for name, _ in read_sse(r)]
    assert "tool_start" in names
    assert "tool_end" in names
    assert names[-1] == "done"
    history = await client.get(_messages_url(cid), headers=headers)
    assert history.json() == [
        {"role": "user", "content": "say hi", "tools": []},
        {"role": "assistant", "content": "It said hi", "tools": ["echo"]},
    ]


@pytest.mark.parametrize(
    "chat_model",
    [
        ScriptedChatModel(
            replies=[
                tool_call_reply("echo", {"text": "hi"}, "c1", content="Let me check."),
                AIMessage("answer"),
            ]
        )
    ],
)
@pytest.mark.parametrize("tools", [[echo]])
async def test_transcript_skips_preamble_of_tool_call_message(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)
    await client.post(_messages_url(cid), json={"content": "say hi"}, headers=headers)
    history = await client.get(_messages_url(cid), headers=headers)
    assert history.json() == [
        {"role": "user", "content": "say hi", "tools": []},
        {"role": "assistant", "content": "answer", "tools": ["echo"]},
    ]
