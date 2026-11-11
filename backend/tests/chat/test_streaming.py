"""Unit tests for ``stream_agent_events`` (no HTTP, no database)."""

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.chat.streaming import MODEL_ERROR_MESSAGE, stream_agent_events
from tests.fakes import ScriptedChatModel, echo, fake_search_documents, tool_call_reply, whoami

CONFIG: Any = {"configurable": {"thread_id": "thread-1"}}


class FailingChatModel(GenericFakeChatModel):
    """A fake model whose every call fails as if the model server were down."""

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        raise ConnectionError("model server unreachable")
        yield  # pragma: no cover  (makes this an async generator)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise ConnectionError("model server unreachable")


class NonStreamingChatModel(BaseChatModel):
    """A fake model that can only answer in one piece (no ``_stream``/``_astream``).

    Like providers without token streaming: LangGraph's messages mode then
    emits the whole reply once, as a plain ``AIMessage``.
    """

    reply: str

    @property
    def _llm_type(self) -> str:
        return "non-streaming-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.reply))])


async def _collect(graph: Any, text: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.event or "", json.loads(event.data))
        async for event in stream_agent_events(graph, "thread-1", text, "user-1")
    ]


async def test_stream_yields_tokens_then_done() -> None:
    model = GenericFakeChatModel(messages=iter([AIMessage("Hello there")]))
    graph = build_graph(model, InMemorySaver())

    events = await _collect(graph, "hi")

    tokens = [data["text"] for name, data in events if name == "token"]
    assert len(tokens) > 1  # streamed in pieces, not as one blob
    assert "".join(tokens) == "Hello there"
    assert events[-1] == ("done", {"content": "Hello there"})


async def test_stream_non_streaming_model_still_yields_reply() -> None:
    graph = build_graph(NonStreamingChatModel(reply="Hello there"), InMemorySaver())

    events = await _collect(graph, "hi")

    tokens = [data["text"] for name, data in events if name == "token"]
    assert "".join(tokens) == "Hello there"
    assert events[-1] == ("done", {"content": "Hello there"})


async def test_stream_model_failure_yields_error_event() -> None:
    graph = build_graph(FailingChatModel(messages=iter([])), InMemorySaver())

    events = await _collect(graph, "hi")

    assert events[-1] == ("error", {"message": MODEL_ERROR_MESSAGE})
    assert [name for name, _ in events].count("error") == 1
    assert "done" not in [name for name, _ in events]
    assert (
        MODEL_ERROR_MESSAGE
        == "The assistant couldn't respond. Is the model server running? Please try again."
    )


async def test_failed_run_does_not_persist_user_message() -> None:
    saver = InMemorySaver()
    config: Any = {"configurable": {"thread_id": "thread-1"}}
    failing = build_graph(FailingChatModel(messages=iter([])), saver)

    events = await _collect(failing, "hi")

    assert events[-1] == ("error", {"message": MODEL_ERROR_MESSAGE})
    assert (await failing.aget_state(config)).values.get("messages", []) == []

    # A retry of the same text on the same thread must not leave a duplicate.
    working = build_graph(GenericFakeChatModel(messages=iter([AIMessage("ok")])), saver)
    events = await _collect(working, "hi")

    assert events[-1] == ("done", {"content": "ok"})
    messages = (await working.aget_state(config)).values["messages"]
    assert [(type(m), m.content) for m in messages] == [(HumanMessage, "hi"), (AIMessage, "ok")]


def _tool_graph(replies: list[AIMessage | Exception], saver: InMemorySaver | None = None) -> Any:
    model = ScriptedChatModel(replies=replies)
    return build_graph(model, saver or InMemorySaver(), tools=[echo])


async def _state_messages(graph: Any) -> list[BaseMessage]:
    return list((await graph.aget_state(CONFIG)).values.get("messages", []))


async def test_tool_events_are_streamed_in_order() -> None:
    graph = _tool_graph([tool_call_reply("echo", {"text": "hi"}, "c1"), AIMessage("It said hi")])

    events = await _collect(graph, "say hi")

    names = [n for n, _ in events]
    assert names[:2] == ["tool_start", "tool_end"] and names[-1] == "done"
    assert events[0][1] == {"id": "c1", "name": "echo", "input": {"text": "hi"}}
    assert events[1][1] == {"id": "c1", "name": "echo", "output": "hi"}
    assert "".join(d["text"] for n, d in events if n == "token") == "It said hi"
    assert events[-1][1] == {"content": "It said hi"}


async def test_tool_output_is_truncated_to_500_chars() -> None:
    graph = _tool_graph([tool_call_reply("echo", {"text": "x" * 2000}, "c1"), AIMessage("ok")])

    events = await _collect(graph, "long")

    (tool_end,) = [d for n, d in events if n == "tool_end"]
    assert tool_end["output"] == "x" * 500


async def test_tool_message_text_never_streams_as_tokens() -> None:
    graph = _tool_graph([tool_call_reply("echo", {"text": "SECRET"}, "c1"), AIMessage("done")])

    events = await _collect(graph, "secret")

    assert not any("SECRET" in d["text"] for n, d in events if n == "token")
    assert events[-1] == ("done", {"content": "done"})


async def test_preamble_before_tool_call_is_not_in_done() -> None:
    graph = _tool_graph(
        [
            tool_call_reply("echo", {"text": "hi"}, "c1", content="Let me check. "),
            AIMessage("The answer is hi"),
        ]
    )

    events = await _collect(graph, "check")

    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert tokens.startswith("Let me check.")  # the preamble still streams live
    assert events[-1] == ("done", {"content": "The answer is hi"})


async def test_unknown_tool_reports_error_and_run_continues() -> None:
    graph = _tool_graph([tool_call_reply("nonexistent", {}, "c1"), AIMessage("Sorry")])

    events = await _collect(graph, "x")

    (tool_end,) = [d for n, d in events if n == "tool_end"]
    assert tool_end["id"] == "c1"
    assert "nonexistent" in tool_end["output"]
    assert events[-1] == ("done", {"content": "Sorry"})


async def test_failure_after_tool_call_rolls_back_whole_turn() -> None:
    saver = InMemorySaver()
    graph = _tool_graph(
        [tool_call_reply("echo", {"text": "hi"}, "c1"), ConnectionError("model server down")], saver
    )

    events = await _collect(graph, "x")

    assert events[-1] == ("error", {"message": MODEL_ERROR_MESSAGE})
    assert await _state_messages(graph) == []

    model = ScriptedChatModel(replies=[AIMessage("ok")])
    working = build_graph(model, saver, tools=[echo])
    events = await _collect(working, "x")

    assert events[-1] == ("done", {"content": "ok"})
    assert not any(isinstance(m, ToolMessage) for m in model.calls[0])
    messages = await _state_messages(working)
    assert [(type(m), m.content) for m in messages] == [(HumanMessage, "x"), (AIMessage, "ok")]


async def test_client_disconnect_mid_turn_rolls_back() -> None:
    graph = _tool_graph([tool_call_reply("echo", {"text": "hi"}, "c1"), AIMessage("never")])

    agen = stream_agent_events(graph, "thread-1", "x", "user-1")
    async for event in agen:
        if event.event == "tool_end":
            break
    await agen.aclose()

    assert await _state_messages(graph) == []


async def test_cancellation_mid_turn_rolls_back() -> None:
    # sse-starlette cancels the consumer on disconnect: the CancelledError lands inside
    # the generator, and anyio keeps re-raising it at every unshielded await.
    graph = _tool_graph([tool_call_reply("echo", {"text": "hi"}, "c1"), AIMessage("never")])

    with anyio.CancelScope() as scope:
        async for event in stream_agent_events(graph, "thread-1", "x", "user-1"):
            if event.event == "tool_end":
                scope.cancel()

    assert scope.cancelled_caught
    assert await _state_messages(graph) == []


async def test_disconnect_after_answer_keeps_turn() -> None:
    graph = _tool_graph([AIMessage("answer")])

    agen = stream_agent_events(graph, "thread-1", "x", "user-1")
    async for event in agen:
        if event.event == "done":
            break
    await agen.aclose()

    messages = await _state_messages(graph)
    assert [(type(m), m.content) for m in messages] == [(HumanMessage, "x"), (AIMessage, "answer")]


async def test_user_id_reaches_configurable() -> None:
    model = ScriptedChatModel(replies=[tool_call_reply("whoami", {}, "c1"), AIMessage("ok")])
    graph = build_graph(model, InMemorySaver(), tools=[whoami])

    events = await _collect(graph, "who am i")

    (tool_end,) = [d for n, d in events if n == "tool_end"]
    assert tool_end["output"] == "user-1"


async def test_citations_event_is_deduplicated_and_sent_right_before_done() -> None:
    model = ScriptedChatModel(
        replies=[
            tool_call_reply("search_documents", {"query": "a"}, "c1"),
            tool_call_reply("search_documents", {"query": "b"}, "c2"),
            AIMessage("From your notes"),
        ]
    )
    graph = build_graph(model, InMemorySaver(), tools=[fake_search_documents])

    events = await _collect(graph, "what do my notes say")

    names = [n for n, _ in events]
    assert names.count("citations") == 1
    assert names[-2:] == ["citations", "done"]
    (items,) = [d["items"] for n, d in events if n == "citations"]
    assert [i["chunk_index"] for i in items] == [0, 1]
    assert items[0] == {
        "document_id": "doc-1",
        "filename": "notes.txt",
        "chunk_index": 0,
        "snippet": "chunk 0",
    }


async def test_no_citations_event_without_search_documents() -> None:
    graph = _tool_graph([tool_call_reply("echo", {"text": "hi"}, "c1"), AIMessage("ok")])

    events = await _collect(graph, "say hi")

    assert "citations" not in [n for n, _ in events]
