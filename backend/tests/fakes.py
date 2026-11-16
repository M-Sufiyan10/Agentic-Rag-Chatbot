"""Test doubles for the agent: a scripted chat model and a trivial tool."""

import json
import re
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    BaseCallbackHandler,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool, tool
from pydantic import SkipValidation


@tool
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@tool
def whoami(config: RunnableConfig) -> str:
    """Return the user id the server put in the run config."""
    return str(config["configurable"]["user_id"])


def _citation(chunk_index: int) -> dict[str, Any]:
    return {
        "document_id": "doc-1",
        "filename": "notes.txt",
        "chunk_index": chunk_index,
        "snippet": f"chunk {chunk_index}",
    }


@tool(name_or_callable="search_documents", response_format="content_and_artifact")
def fake_search_documents(query: str) -> tuple[str, list[dict[str, Any]]]:
    """Fixed results: query "a" finds chunks 0 and 1, anything else finds chunk 1 only."""
    items = [_citation(0), _citation(1)] if query == "a" else [_citation(1)]
    return f"found {len(items)}", items


def tool_call_reply(name: str, args: dict[str, Any], call_id: str, content: str = "") -> AIMessage:
    """An AI reply that asks for one tool call."""
    return AIMessage(content, tool_calls=[{"name": name, "args": args, "id": call_id}])


class ScriptedChatModel(BaseChatModel):
    """Replays `replies` in order; an `Exception` entry is raised when reached.

    Unlike `GenericFakeChatModel` it streams tool-call replies correctly. Records
    each call's input in `calls` and the bound tool names in `call_tools` (None
    when called unbound). The copy returned by `bind_tools` shares the reply
    cursor and recorders with the original.
    """

    # Not validated: pydantic would try to coerce an Exception entry into an AIMessage and crash.
    replies: SkipValidation[list[AIMessage | Exception]]
    calls: list[list[BaseMessage]] = []
    call_tools: list[list[str] | None] = []
    cursor: list[int] = [0]
    bound: list[str] | None = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:
        names = [t.name if isinstance(t, BaseTool) else str(t) for t in tools]
        return self.model_copy(update={"bound": names})

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        self.call_tools.append(self.bound)
        index = self.cursor[0]
        if index >= len(self.replies):
            raise AssertionError("ScriptedChatModel ran out of replies")
        self.cursor[0] += 1
        reply = self.replies[index]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        reply = self._next(messages)
        if reply.tool_calls:
            chunk = AIMessageChunk(
                content=str(reply.content),
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": json.dumps(call["args"]),
                        "id": call["id"],
                        "index": i,
                    }
                    for i, call in enumerate(reply.tool_calls)
                ],
            )
            yield ChatGenerationChunk(message=chunk)
            return
        for piece in re.findall(r"\S+\s*", str(reply.content)):
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))


class RecordingHandler(BaseCallbackHandler):
    """Records the name and metadata of every chain start."""

    def __init__(self) -> None:
        self.chains: list[tuple[str | None, dict[str, Any]]] = []

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        metadata: dict[str, Any] | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.chains.append((name, dict(metadata or {})))


class FakeTracing:
    """A `Tracing` double: records trace ids and hands out one recording handler."""

    status = "ok"

    def __init__(self) -> None:
        self.trace_ids: list[str] = []
        self.handler = RecordingHandler()

    def callbacks(self, trace_id: str) -> list[BaseCallbackHandler]:
        self.trace_ids.append(trace_id)
        return [self.handler]

    async def check(self) -> str:
        return self.status

    async def record_feedback(
        self, *, turn_id: str, positive: bool, first_voted_at: datetime
    ) -> None:
        return None

    async def delete_feedback(self, *, turn_id: str) -> None:
        return None

    def shutdown(self) -> None:
        return None
