"""Conversation endpoints: create, list, rename and delete (own conversations only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import User
from app.chat.deps import GraphDep, in_progress_conversations
from app.conversations import service
from app.conversations.schemas import ConversationCreate, ConversationOut, ConversationUpdate
from app.db import get_session

router = APIRouter(prefix="/conversations", tags=["conversations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, session: SessionDep, user: CurrentUser
) -> ConversationOut:
    conversation = await service.create_conversation(session, user.id, body.title)
    return ConversationOut.model_validate(conversation)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(session: SessionDep, user: CurrentUser) -> list[ConversationOut]:
    conversations = await service.list_conversations(session, user.id)
    return [ConversationOut.model_validate(c) for c in conversations]


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: UUID, body: ConversationUpdate, session: SessionDep, user: CurrentUser
) -> ConversationOut:
    conversation = await service.rename_conversation(session, user.id, conversation_id, body.title)
    return ConversationOut.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID, session: SessionDep, user: CurrentUser, graph: GraphDep
) -> Response:
    """Delete the conversation and its chat history (the LangGraph thread)."""
    await service.get_owned_conversation(session, user.id, conversation_id)  # 404 if not owned
    # Deleting the thread under a running reply would let the run re-create it.
    if conversation_id in in_progress_conversations:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A reply is in progress. Try again when it finishes."
        )
    await service.delete_conversation(session, user.id, conversation_id)
    checkpointer = graph.checkpointer
    if not isinstance(checkpointer, BaseCheckpointSaver):  # build_graph always sets one
        raise RuntimeError("The agent graph has no checkpointer")
    await checkpointer.adelete_thread(str(conversation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
