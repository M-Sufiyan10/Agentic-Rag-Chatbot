"""Conversation endpoints: create, list, rename and delete (own conversations only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import User
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
    conversation_id: UUID, session: SessionDep, user: CurrentUser
) -> Response:
    await service.delete_conversation(session, user.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
