import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.documents.models import Document, DocumentChunk

VECTOR = [0.1] * 768


async def _seed(session: AsyncSession) -> tuple[User, Document]:
    user = User(email="a@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    doc = Document(
        user_id=user.id,
        filename="a.txt",
        content_type="text/plain",
        size_bytes=5,
        storage_key=f"{user.id}/x.txt",
    )
    session.add(doc)
    await session.flush()
    for i in range(2):
        session.add(
            DocumentChunk(
                document_id=doc.id,
                user_id=user.id,
                chunk_index=i,
                content=f"chunk {i}",
                embedding=VECTOR,
                embedding_model="fake",
            )
        )
    await session.commit()
    return user, doc


async def test_defaults_and_user_delete_cascades(db_session: AsyncSession) -> None:
    user, doc = await _seed(db_session)
    await db_session.refresh(doc)
    assert doc.status == "processing"
    assert doc.chunk_count == 0
    assert doc.error is None

    await db_session.delete(user)
    await db_session.commit()
    for model in (Document, DocumentChunk):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


async def test_duplicate_chunk_index_rejected(db_session: AsyncSession) -> None:
    user, doc = await _seed(db_session)
    db_session.add(
        DocumentChunk(
            id=uuid.uuid4(),
            document_id=doc.id,
            user_id=user.id,
            chunk_index=0,
            content="dup",
            embedding=VECTOR,
            embedding_model="fake",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
