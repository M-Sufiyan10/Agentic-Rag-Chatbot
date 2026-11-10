"""Turn a LangGraph run into Server-Sent Events (SSE) for the browser.

How the streaming works:

1. ``graph.astream(..., stream_mode="messages")`` runs the graph and yields
   ``(message_chunk, metadata)`` pairs *while the model is still generating*.
   LangGraph hooks into the chat model's callbacks, so even though our node
   calls ``model.ainvoke(...)``, the model streams and each new token arrives
   here as an ``AIMessageChunk``. (A model without streaming support delivers
   its whole reply at once as a single ``AIMessage`` instead.) ``metadata``
   says which node produced it (``metadata["langgraph_node"]``).
2. Each non-empty text chunk becomes a ``token`` event; when the run ends we
   send one ``done`` event with the full reply. The checkpointer has already
   saved the user message and the reply by then.
3. If anything fails (most often: the model server is down), we log the full
   traceback and send a single ``error`` event with a friendly message, so the
   UI can show it instead of hanging.

Event names and payloads follow the SSE contract in the roadmap
(``docs/superpowers/plans/2026-11-10-00-roadmap.md``). Later milestones add
``tool_start``/``tool_end``/``citations`` events: they will be emitted from the
same loop by inspecting other message types (``ToolMessage``, tool-call chunks)
via additional branches in ``_events_for_chunk``.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from sse_starlette import ServerSentEvent

logger = logging.getLogger(__name__)

MODEL_ERROR_MESSAGE = (
    "The assistant couldn't respond. Is the model server running? Please try again."
)


def sse_event(name: str, payload: dict[str, Any]) -> ServerSentEvent:
    """Build one SSE event: ``event: <name>`` plus ``data: <json payload>``."""
    return ServerSentEvent(event=name, data=json.dumps(payload))


def _events_for_chunk(chunk: object, reply_parts: list[str]) -> list[ServerSentEvent]:
    """Map one streamed message chunk to the SSE events it produces.

    Text from AI messages is appended to ``reply_parts`` (for the final ``done``).
    Streaming models yield many ``AIMessageChunk`` pieces; a model that can't
    stream yields its whole reply once as a plain ``AIMessage`` (``AIMessageChunk``
    is a subclass of ``AIMessage``, so one check covers both).
    Anything else (e.g. tool messages in later milestones) produces no events yet.
    """
    if isinstance(chunk, AIMessage):
        text = chunk.text
        if text:
            reply_parts.append(text)
            return [sse_event("token", {"text": text})]
    return []


async def _roll_back_unanswered_message(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    config: RunnableConfig,
    human_message: HumanMessage,
) -> None:
    """Remove ``human_message`` from the thread if no reply followed it.

    Never raises: a failed rollback is logged, and the caller still sends its
    ``error`` event.
    """
    try:
        state = await graph.aget_state(config)
        messages = state.values.get("messages", [])
        if messages and messages[-1].id == human_message.id:
            # as_node="agent" records the update as if the agent node wrote it,
            # so the graph stays at the end of the run instead of re-running it.
            await graph.aupdate_state(
                config, {"messages": [RemoveMessage(id=str(human_message.id))]}, as_node="agent"
            )
    except Exception:
        logger.exception(
            "Could not roll back unanswered message in thread %s",
            config["configurable"]["thread_id"],
        )


async def stream_agent_events(
    graph: CompiledStateGraph[Any, Any, Any, Any], thread_id: str, user_text: str
) -> AsyncIterator[ServerSentEvent]:
    """Run the agent on ``user_text`` in thread ``thread_id`` and yield SSE events.

    Only the *new* user message is sent: the checkpointer loads the earlier
    history of ``thread_id`` before the graph runs and saves the result after.

    Yields ``token`` events (``{"text": ...}``) as the reply is generated, then
    ``done`` (``{"content": <full reply>}``); or, on failure, one ``error``
    event (``{"message": ...}``) instead of ``done``.

    Why there is a rollback on failure: LangGraph saves a checkpoint after the
    ``input`` step, i.e. *before* the ``agent`` node (the model call) runs. If
    the model then fails, the user's message would stay saved with no reply,
    and the UI's Retry would send it again, leaving the same question in the
    history twice. So on failure we delete that message again, but only if it
    is still the last one (no reply was saved). Deleting works through the
    ``add_messages`` reducer: a ``RemoveMessage`` with the message's id removes
    it, which is why we give the input message an explicit id up front.
    """
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    reply_parts: list[str] = []
    human_message = HumanMessage(user_text, id=str(uuid4()))
    try:
        async for chunk, _metadata in graph.astream(
            {"messages": [human_message]}, config, stream_mode="messages"
        ):
            for event in _events_for_chunk(chunk, reply_parts):
                yield event
    except Exception:
        logger.exception("Agent run failed for thread %s", thread_id)
        await _roll_back_unanswered_message(graph, config, human_message)
        yield sse_event("error", {"message": MODEL_ERROR_MESSAGE})
        return
    yield sse_event("done", {"content": "".join(reply_parts)})
