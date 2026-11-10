"""Unit tests for ``stream_agent_events`` (no HTTP, no database)."""

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.chat.streaming import MODEL_ERROR_MESSAGE, stream_agent_events


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


async def _collect(graph: Any, text: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.event or "", json.loads(event.data))
        async for event in stream_agent_events(graph, "thread-1", text)
    ]


async def test_stream_yields_tokens_then_done() -> None:
    model = GenericFakeChatModel(messages=iter([AIMessage("Hello there")]))
    graph = build_graph(model, InMemorySaver())

    events = await _collect(graph, "hi")

    tokens = [data["text"] for name, data in events if name == "token"]
    assert len(tokens) > 1  # streamed in pieces, not as one blob
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
