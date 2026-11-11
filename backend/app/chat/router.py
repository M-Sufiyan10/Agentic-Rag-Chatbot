"""Chat endpoints: send a message (streamed reply over SSE) and read the history."""

from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.background import BackgroundTask

from app.auth.deps import get_current_user
from app.auth.models import User
from app.chat.schemas import MessageIn, MessageOut
from app.chat.streaming import stream_agent_events
from app.conversations.models import Conversation
from app.conversations.service import get_owned_conversation
from app.db import get_session

router = APIRouter(prefix="/conversations", tags=["chat"])

DEFAULT_TITLE = "New chat"
TITLE_LENGTH = 60

# Conversations whose reply is currently streaming. One reply at a time per
# conversation: two concurrent runs on the same LangGraph thread would race on
# its checkpoints. Process-local, which is enough for a single-process server.
in_progress_conversations: set[UUID] = set()

Graph = CompiledStateGraph[Any, Any, Any, Any]


def get_graph(request: Request) -> Graph:
    """Return the compiled agent graph built in the app lifespan (``app.state.graph``)."""
    graph: Graph = request.app.state.graph
    return graph


SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
GraphDep = Annotated[Graph, Depends(get_graph)]


async def _record_new_message(session: AsyncSession, conversation: Conversation, text: str) -> None:
    """Name an untitled conversation after its first message and bump ``updated_at``."""
    if conversation.title == DEFAULT_TITLE:
        conversation.title = text[:TITLE_LENGTH]
    conversation.updated_at = datetime.now(UTC)
    await session.commit()


async def stream_and_release(
    graph: Graph, conversation_id: UUID, text: str
) -> AsyncIterator[ServerSentEvent]:
    """Stream the agent's events, then release ``conversation_id``.

    sse-starlette closes this generator (running ``finally``) when the stream
    ends or the client disconnects. ``aclosing`` closes the inner generator
    first, so its rollback of an incomplete turn has finished before the id is
    released; otherwise the inner generator would only be finalized later by
    the garbage collector, and a new message could start on the thread before
    the rollback ran.
    """
    try:
        async with aclosing(stream_agent_events(graph, str(conversation_id), text)) as events:
            async for event in events:
                yield event
    finally:
        in_progress_conversations.discard(conversation_id)


@router.post("/{conversation_id}/messages", response_class=EventSourceResponse)
async def send_message(
    conversation_id: UUID,
    body: MessageIn,
    session: SessionDep,
    user: CurrentUser,
    graph: GraphDep,
) -> EventSourceResponse:
    """Send a user message and stream the assistant's reply as SSE events.

    The ownership (404) and in-progress (409) checks run here, *before* the
    ``EventSourceResponse`` is returned: once streaming starts, the status code
    (200) has already been sent and errors can only be reported as events.
    """
    conversation = await get_owned_conversation(session, user.id, conversation_id)
    # No ``await`` between this check and the ``add`` below, so two requests
    # on the event loop cannot both pass the check.
    if conversation_id in in_progress_conversations:
        raise HTTPException(status.HTTP_409_CONFLICT, "A reply is already in progress")
    in_progress_conversations.add(conversation_id)
    try:
        await _record_new_message(session, conversation, body.content)
    except BaseException:
        in_progress_conversations.discard(conversation_id)
        raise

    # Belt and braces: if the response is cancelled before the generator ever
    # starts, its ``finally`` never runs, so also release the id in a background
    # task once the response finishes. ``discard`` is idempotent.
    release = BackgroundTask(in_progress_conversations.discard, conversation_id)
    return EventSourceResponse(
        stream_and_release(graph, conversation_id, body.content), background=release
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: UUID, session: SessionDep, user: CurrentUser, graph: GraphDep
) -> list[MessageOut]:
    """Return the conversation transcript, read from the checkpointed graph state.

    Only user and assistant messages with text are returned (no system prompt,
    and in later milestones no tool calls/results). A new thread returns ``[]``.
    """
    await get_owned_conversation(session, user.id, conversation_id)
    config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
    snapshot = await graph.aget_state(config)
    transcript: list[MessageOut] = []
    for message in snapshot.values.get("messages", []):
        if not isinstance(message.content, str) or not message.content:
            continue
        if isinstance(message, HumanMessage):
            transcript.append(MessageOut(role="user", content=message.content))
        elif isinstance(message, AIMessage):
            transcript.append(MessageOut(role="assistant", content=message.content))
    return transcript
