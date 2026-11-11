"""API tests for POST/GET /api/conversations/{id}/messages and thread deletion."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import build_graph
from app.agent.prompts import DOCUMENTS_PROCESSING_NOTE, SYSTEM_PROMPT
from app.auth.models import User
from app.chat import deps as chat_deps
from app.chat import router as chat_router
from app.documents.models import Document
from tests.conftest import FAKE_REPLY, read_sse, register_user
from tests.fakes import ScriptedChatModel, echo, fake_search_documents, tool_call_reply, whoami

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
        {"role": "user", "content": "hi there", "tools": [], "citations": []},
        {"role": "assistant", "content": FAKE_REPLY, "tools": [], "citations": []},
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
        {"role": "user", "content": "hi", "tools": [], "citations": []},
        {"role": "assistant", "content": "ok", "tools": [], "citations": []},
    ]


async def test_disconnect_rolls_back_before_releasing_id() -> None:
    # sse-starlette closes the router's generator on disconnect. The rollback of the
    # incomplete turn must be done by the time that close returns, i.e. before the
    # conversation is released for a new message (no extra event-loop tick here).
    model = ScriptedChatModel(replies=[tool_call_reply("echo", {"text": "hi"}, "c1")])
    graph = build_graph(model, InMemorySaver(), tools=[echo])
    conversation_id = uuid.uuid4()
    chat_deps.in_progress_conversations.add(conversation_id)

    agen = chat_router.stream_and_release(graph, conversation_id, "x", "user-1")
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
        {"role": "user", "content": "say hi", "tools": [], "citations": []},
        {"role": "assistant", "content": "It said hi", "tools": ["echo"], "citations": []},
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
        {"role": "user", "content": "say hi", "tools": [], "citations": []},
        {"role": "assistant", "content": "answer", "tools": ["echo"], "citations": []},
    ]


@pytest.mark.parametrize(
    "chat_model",
    [ScriptedChatModel(replies=[tool_call_reply("whoami", {}, "c1"), AIMessage("ok")])],
)
@pytest.mark.parametrize("tools", [[whoami]])
async def test_authenticated_user_id_reaches_tools(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    me = await client.get("/api/auth/me", headers=headers)
    cid = await _new_conversation(client, headers)

    r = await client.post(_messages_url(cid), json={"content": "who am i"}, headers=headers)

    (tool_end,) = [data for name, data in read_sse(r) if name == "tool_end"]
    assert tool_end["output"] == me.json()["id"]


@pytest.mark.parametrize(
    "chat_model",
    [
        ScriptedChatModel(
            replies=[
                tool_call_reply("search_documents", {"query": "a"}, "c1"),
                tool_call_reply("search_documents", {"query": "b"}, "c2"),
                AIMessage("From your notes"),
            ]
        )
    ],
)
@pytest.mark.parametrize("tools", [[fake_search_documents]])
async def test_citations_streamed_and_listed_in_transcript(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    cid = await _new_conversation(client, headers)

    r = await client.post(_messages_url(cid), json={"content": "notes?"}, headers=headers)

    events = read_sse(r)
    assert [n for n, _ in events][-2:] == ["citations", "done"]
    history = (await client.get(_messages_url(cid), headers=headers)).json()
    user, assistant = history
    assert user["citations"] == []
    assert [c["chunk_index"] for c in assistant["citations"]] == [0, 1]
    assert assistant["citations"][0]["filename"] == "notes.txt"
    assert assistant["tools"] == ["search_documents", "search_documents"]


async def _add_document(db: AsyncSession, email: str, filename: str, status: str) -> None:
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    db.add(
        Document(
            user_id=user.id,
            filename=filename,
            content_type="text/markdown",
            size_bytes=1,
            storage_key=f"{user.id}/{uuid.uuid4()}.md",
            status=status,
        )
    )
    await db.commit()


def _system_prompt(messages: list[BaseMessage]) -> str:
    assert isinstance(messages[0], SystemMessage)
    return str(messages[0].content)


@pytest.mark.parametrize(
    "chat_model", [ScriptedChatModel(replies=[AIMessage("for bob"), AIMessage("for alice")])]
)
async def test_documents_note_lists_only_the_senders_documents(
    client: httpx.AsyncClient, db_session: AsyncSession, chat_model: ScriptedChatModel
) -> None:
    alice = await register_user(client, email="alice@example.com")
    bob = await register_user(client, email="bob@example.com")
    await _add_document(db_session, "alice@example.com", "alpha-secret.md", "ready")
    bob_cid = await _new_conversation(client, bob)
    alice_cid = await _new_conversation(client, alice)

    await client.post(_messages_url(bob_cid), json={"content": "what's in it?"}, headers=bob)
    await client.post(_messages_url(alice_cid), json={"content": "what's in it?"}, headers=alice)

    bob_prompt, alice_prompt = (_system_prompt(call) for call in chat_model.calls)
    assert bob_prompt == SYSTEM_PROMPT
    assert "alpha-secret" not in bob_prompt
    assert alice_prompt.startswith(SYSTEM_PROMPT + "\n\n")
    assert '"alpha-secret.md"' in alice_prompt


@pytest.mark.parametrize("chat_model", [ScriptedChatModel(replies=[AIMessage("wait")])])
async def test_documents_note_for_processing_only(
    client: httpx.AsyncClient, db_session: AsyncSession, chat_model: ScriptedChatModel
) -> None:
    headers = await register_user(client, email="alice@example.com")
    await _add_document(db_session, "alice@example.com", "big.pdf", "processing")
    await _add_document(db_session, "alice@example.com", "scan.pdf", "failed")
    cid = await _new_conversation(client, headers)

    await client.post(_messages_url(cid), json={"content": "summarise it"}, headers=headers)

    assert _system_prompt(chat_model.calls[0]) == (
        SYSTEM_PROMPT + "\n\n" + DOCUMENTS_PROCESSING_NOTE
    )
