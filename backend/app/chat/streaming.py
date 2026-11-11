"""Turn a LangGraph run into Server-Sent Events (SSE) for the browser.

Event names and payloads follow the SSE contract in the roadmap
(``docs/superpowers/plans/2026-11-10-00-roadmap.md``).

Streaming
---------

The graph is run with ``stream_mode=["messages", "updates"]``, which yields
``(mode, payload)`` pairs:

* ``"messages"``: ``(chunk, metadata)`` *while the model is still generating*.
  LangGraph hooks into the chat model's callbacks, so even though the agent
  node calls ``model.ainvoke(...)``, each new token arrives here as an
  ``AIMessageChunk`` (a model that can't stream delivers its whole reply once
  as a plain ``AIMessage``). Only chunks whose ``metadata["langgraph_node"]``
  is ``"agent"`` and that are ``AIMessage``s with text become ``token`` events.
  The ``tools`` node's ``ToolMessage``s also show up in this mode; they never
  become tokens.
* ``"updates"``: ``{node_name: {"messages": [...]}}`` after each node finishes.
  Under ``"agent"``, each AI message's ``tool_calls`` become one
  ``tool_start`` each, in order: ``{"id", "name", "input": args}``. Under
  ``"tools"``, each ``ToolMessage`` becomes a ``tool_end``:
  ``{"id": tool_call_id, "name": name, "output": text[:TOOL_OUTPUT_LIMIT]}``,
  where the text is ``ToolMessage.text``.

``done.content`` is the text of the final AI answer only. Text streamed in an
earlier round, before a tool call (a "Let me check..." preamble), is not
included: the accumulated parts are reset on every ``tool_start``.

Failure and abandonment
-----------------------

LangGraph checkpoints after every step: after the input step (the user's
message), after each ``agent`` reply and after each ``tools`` step. A run that
stops halfway therefore leaves a partial turn in the thread: the user's
message, maybe a tool call, maybe its result, but no answer. A turn like that
breaks the next one (the UI's Retry would repeat the question; an AI tool call
without its result is rejected by most model APIs), so it is removed.

* **A turn is complete** when the last message in state, after the user's
  message, is an ``AIMessage`` without ``tool_calls``.
* **On any failure** (an exception) **or on abandonment**
  (``asyncio.CancelledError`` or ``GeneratorExit``, when the client
  disconnects): if the turn is incomplete, every message from the user's
  message onward is removed with ``RemoveMessage``s in one
  ``aupdate_state(..., as_node="agent")``. The rollback runs inside
  ``anyio.CancelScope(shield=True)`` so a cancellation can't interrupt it.
  Then: on an exception, one ``error`` event is sent; on abandonment, the
  exception is re-raised.
* If the turn completed (for example, the client disconnected after the final
  answer was saved), nothing is removed.

Deleting works through the ``add_messages`` reducer: a ``RemoveMessage`` with a
message's id removes it, which is why the user's message gets an explicit id
up front. ``as_node="agent"`` records the update as if the agent node wrote it,
so the graph stays at the end of the run instead of re-running it.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, cast
from uuid import uuid4

import anyio
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from sse_starlette import ServerSentEvent

logger = logging.getLogger(__name__)

MODEL_ERROR_MESSAGE = (
    "The assistant couldn't respond. Is the model server running? Please try again."
)
TOOL_OUTPUT_LIMIT = 500


def sse_event(name: str, payload: dict[str, Any]) -> ServerSentEvent:
    """Build one SSE event: ``event: <name>`` plus ``data: <json payload>``."""
    return ServerSentEvent(event=name, data=json.dumps(payload))


def _events_for_message_chunk(
    chunk: object, metadata: dict[str, Any], reply_parts: list[str]
) -> list[ServerSentEvent]:
    """Map one ``"messages"``-mode chunk to ``token`` events.

    Only text from AI messages produced by the ``agent`` node counts; its text
    is also appended to ``reply_parts`` (for the final ``done``).
    ``AIMessageChunk`` is a subclass of ``AIMessage``, so one check covers
    streaming and non-streaming models.
    """
    if metadata.get("langgraph_node") != "agent" or not isinstance(chunk, AIMessage):
        return []
    text = chunk.text
    if not text:
        return []
    reply_parts.append(text)
    return [sse_event("token", {"text": text})]


def _events_for_update(update: dict[str, Any], reply_parts: list[str]) -> list[ServerSentEvent]:
    """Map one ``"updates"``-mode payload to ``tool_start``/``tool_end`` events.

    A ``tool_start`` clears ``reply_parts``: text before a tool call was a
    preamble, not part of the final answer.
    """
    events: list[ServerSentEvent] = []
    for node, node_update in update.items():
        if not isinstance(node_update, dict):  # e.g. a node that returned None
            continue
        for message in node_update.get("messages", []):
            if node == "agent" and isinstance(message, AIMessage):
                for call in message.tool_calls:
                    reply_parts.clear()
                    events.append(
                        sse_event(
                            "tool_start",
                            {"id": call["id"], "name": call["name"], "input": call["args"]},
                        )
                    )
            elif node == "tools" and isinstance(message, ToolMessage):
                events.append(
                    sse_event(
                        "tool_end",
                        {
                            "id": message.tool_call_id,
                            "name": message.name,
                            "output": message.text[:TOOL_OUTPUT_LIMIT],
                        },
                    )
                )
    return events


async def roll_back_incomplete_turn(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    config: RunnableConfig,
    human_message: HumanMessage,
) -> None:
    """Remove ``human_message`` and everything after it, unless the turn completed.

    Never raises: a failed rollback is logged, and the caller carries on
    (sends its ``error`` event or re-raises the cancellation).
    """
    try:
        latest = await graph.aget_state(config)
        if "checkpoint_id" not in latest.config.get("configurable", {}):
            return  # nothing saved in this thread yet
        # Read the latest checkpoint as committed. Without a checkpoint_id the
        # snapshot is "live": it also shows writes of tasks that finished in a
        # step the run never committed (e.g. a tool result when the client left
        # mid-step). Those writes are dropped once we update from this checkpoint,
        # and removing their messages would fail as unknown ids.
        state = await graph.aget_state(latest.config)
        messages = state.values.get("messages", [])
        ids = [message.id for message in messages]
        if human_message.id not in ids:
            return
        turn = messages[ids.index(human_message.id) :]
        last = turn[-1]
        if isinstance(last, AIMessage) and not last.tool_calls:
            return
        await graph.aupdate_state(
            state.config,
            {"messages": [RemoveMessage(id=str(message.id)) for message in turn]},
            as_node="agent",
        )
    except Exception:
        logger.exception(
            "Could not roll back incomplete turn in thread %s",
            config["configurable"]["thread_id"],
        )


async def stream_agent_events(
    graph: CompiledStateGraph[Any, Any, Any, Any], thread_id: str, user_text: str, user_id: str
) -> AsyncGenerator[ServerSentEvent]:
    """Run the agent on ``user_text`` in thread ``thread_id`` and yield SSE events.

    ``user_id`` is the authenticated user's id. It goes into the run config
    (``configurable.user_id``), where per-user tools such as
    ``search_documents`` read it; it is never taken from the model.

    Only the *new* user message is sent: the checkpointer loads the earlier
    history of ``thread_id`` before the graph runs and saves the result after.

    Yields ``token``, ``tool_start`` and ``tool_end`` events as the run
    progresses, then ``done`` (``{"content": <final answer>}``); or, on
    failure, one ``error`` event (``{"message": ...}``) instead of ``done``.
    See the module docstring for the exact mapping and the rollback rules.
    """
    config: RunnableConfig = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    reply_parts: list[str] = []
    human_message = HumanMessage(user_text, id=str(uuid4()))
    # With a list of modes, astream yields (mode, payload) pairs; its declared type can't say so.
    stream = cast(
        AsyncGenerator[tuple[str, Any]],
        graph.astream({"messages": [human_message]}, config, stream_mode=["messages", "updates"]),
    )

    async def clean_up() -> None:
        # Close the run first (it may still be writing a checkpoint), then roll back.
        with anyio.CancelScope(shield=True):
            await stream.aclose()
            await roll_back_incomplete_turn(graph, config, human_message)

    try:
        async for mode, payload in stream:
            if mode == "messages":
                chunk, metadata = payload
                events = _events_for_message_chunk(chunk, metadata, reply_parts)
            else:
                events = _events_for_update(payload, reply_parts)
            for event in events:
                yield event
    except Exception:
        logger.exception("Agent run failed for thread %s", thread_id)
        await clean_up()
        yield sse_event("error", {"message": MODEL_ERROR_MESSAGE})
        return
    except (asyncio.CancelledError, GeneratorExit):
        await clean_up()
        raise
    yield sse_event("done", {"content": "".join(reply_parts)})
