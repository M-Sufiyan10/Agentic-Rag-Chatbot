"""Thumbs up/down feedback on a completed assistant turn."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from langchain_core.runnables import RunnableConfig
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import User
from app.chat.deps import Graph, GraphDep
from app.chat.transcript import completed_turn_ids
from app.conversations.service import get_owned_conversation
from app.db import get_session
from app.feedback.models import MessageFeedback
from app.feedback.schemas import FeedbackIn, FeedbackOut
from app.observability import Tracing

router = APIRouter(prefix="/conversations", tags=["feedback"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


async def _require_completed_turn(
    session: AsyncSession, graph: Graph, user: User, conversation_id: UUID, turn_id: UUID
) -> None:
    """404 unless the conversation is the caller's and the turn finished with an answer."""
    await get_owned_conversation(session, user.id, conversation_id)
    config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
    snapshot = await graph.aget_state(config)
    if str(turn_id) not in completed_turn_ids(snapshot.values.get("messages", [])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")


@router.put("/{conversation_id}/messages/{turn_id}/feedback", response_model=FeedbackOut)
async def put_feedback(
    conversation_id: UUID,
    turn_id: UUID,
    body: FeedbackIn,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    graph: GraphDep,
) -> FeedbackOut:
    """Set (or change) the caller's vote; the first vote's time is kept."""
    await _require_completed_turn(session, graph, user, conversation_id, turn_id)
    positive = body.value == "up"
    row = await session.scalar(
        select(MessageFeedback).where(
            MessageFeedback.conversation_id == conversation_id, MessageFeedback.turn_id == turn_id
        )
    )
    if row is None:
        row = MessageFeedback(
            conversation_id=conversation_id, user_id=user.id, turn_id=turn_id, positive=positive
        )
        session.add(row)
    else:
        row.positive = positive
    await session.commit()
    await session.refresh(row)
    tracing: Tracing | None = request.app.state.tracing
    if tracing is not None:
        await tracing.record_feedback(
            turn_id=str(turn_id), positive=positive, first_voted_at=row.created_at
        )
    return FeedbackOut(value=body.value)


@router.delete("/{conversation_id}/messages/{turn_id}/feedback", status_code=204)
async def delete_feedback(
    conversation_id: UUID,
    turn_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    graph: GraphDep,
) -> Response:
    """Clear the caller's vote. Idempotent."""
    await _require_completed_turn(session, graph, user, conversation_id, turn_id)
    await session.execute(
        delete(MessageFeedback).where(
            MessageFeedback.conversation_id == conversation_id, MessageFeedback.turn_id == turn_id
        )
    )
    await session.commit()
    tracing: Tracing | None = request.app.state.tracing
    if tracing is not None:
        await tracing.delete_feedback(turn_id=str(turn_id))
    return Response(status_code=204)
