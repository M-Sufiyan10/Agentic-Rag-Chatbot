"""Conversation data access. Every query is scoped to the owning user."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation


async def get_owned_conversation(
    session: AsyncSession, user_id: UUID, conversation_id: UUID
) -> Conversation:
    """Return the user's conversation, or raise 404 (also for other users' ids)."""
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


async def create_conversation(
    session: AsyncSession, user_id: UUID, title: str | None
) -> Conversation:
    conversation = Conversation(user_id=user_id)
    if title is not None:
        conversation.title = title
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def list_conversations(session: AsyncSession, user_id: UUID) -> list[Conversation]:
    result = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    )
    return list(result)


async def rename_conversation(
    session: AsyncSession, user_id: UUID, conversation_id: UUID, title: str
) -> Conversation:
    conversation = await get_owned_conversation(session, user_id, conversation_id)
    conversation.title = title
    conversation.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def delete_conversation(session: AsyncSession, user_id: UUID, conversation_id: UUID) -> None:
    conversation = await get_owned_conversation(session, user_id, conversation_id)
    await session.delete(conversation)
    await session.commit()
