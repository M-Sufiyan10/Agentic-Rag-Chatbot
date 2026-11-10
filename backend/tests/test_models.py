import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.conversations.models import Conversation


async def test_user_email_is_unique(db_session: AsyncSession) -> None:
    db_session.add(User(email="a@example.com", password_hash="x"))
    await db_session.commit()
    db_session.add(User(email="a@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_deleting_user_cascades_conversations(db_session: AsyncSession) -> None:
    u = User(email="b@example.com", password_hash="x")
    db_session.add(u)
    await db_session.flush()
    db_session.add(Conversation(user_id=u.id))
    await db_session.commit()
    await db_session.delete(u)
    await db_session.commit()
    count = await db_session.scalar(select(func.count()).select_from(Conversation))
    assert count == 0
