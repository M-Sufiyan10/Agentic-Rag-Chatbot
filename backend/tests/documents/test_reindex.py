import uuid
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.config import Settings
from app.documents.ingestion import ingest_document
from app.documents.models import Document, DocumentChunk
from app.documents.reindex import ReindexResult, reindex, resize_embedding_column
from app.documents.storage import LocalFileStorage

COLUMN_DIMENSION = (
    "SELECT atttypmod FROM pg_attribute "
    "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
)


@pytest.fixture
def sessionmaker(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def _ready_document(
    db: AsyncSession,
    storage: LocalFileStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    embeddings: Embeddings,
    user: User,
    filename: str,
) -> uuid.UUID:
    doc_id = uuid.uuid4()
    key = f"{user.id}/{doc_id}.txt"
    await storage.save(key, f"Notes in {filename}.".encode())
    db.add(
        Document(
            id=doc_id,
            user_id=user.id,
            filename=filename,
            content_type="text/plain",
            size_bytes=10,
            storage_key=key,
            status="processing",
        )
    )
    await db.commit()
    old = settings.model_copy(update={"embedding_model": "old"})
    await ingest_document(
        doc_id, sessionmaker=sessionmaker, storage=storage, embeddings=embeddings, settings=old
    )
    return doc_id


async def _setup(
    db: AsyncSession,
    tmp_path: Path,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    embeddings: Embeddings,
) -> tuple[LocalFileStorage, list[uuid.UUID]]:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.commit()
    storage = LocalFileStorage(tmp_path)
    ids = [
        await _ready_document(db, storage, sessionmaker, settings, embeddings, user, name)
        for name in ("a.txt", "b.txt")
    ]
    return storage, ids


async def _assert_reindexed(db: AsyncSession, ids: list[uuid.UUID], model: str) -> None:
    db.expire_all()
    chunks = list((await db.execute(select(DocumentChunk))).scalars())
    assert chunks
    assert {c.embedding_model for c in chunks} == {model}
    assert {c.document_id for c in chunks} == set(ids)
    docs = list((await db.execute(select(Document))).scalars())
    assert {d.status for d in docs} == {"ready"}
    assert all(d.chunk_count == 1 for d in docs)


async def test_reindex_same_dimensions_re_embeds_with_current_model(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    embeddings: Embeddings,
    tmp_path: Path,
) -> None:
    storage, ids = await _setup(db_session, tmp_path, sessionmaker, test_settings, embeddings)

    result = await reindex(
        sessionmaker=sessionmaker, storage=storage, embeddings=embeddings, settings=test_settings
    )

    assert result == ReindexResult(documents=2, resized_from=None)
    await _assert_reindexed(db_session, ids, "nomic-embed-text")


async def test_reindex_resizes_column_when_dimensions_change(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    embeddings: Embeddings,
    tmp_path: Path,
) -> None:
    storage, ids = await _setup(db_session, tmp_path, sessionmaker, test_settings, embeddings)
    small_settings = test_settings.model_copy(update={"embedding_dimensions": 384})

    try:
        result = await reindex(
            sessionmaker=sessionmaker,
            storage=storage,
            embeddings=DeterministicFakeEmbedding(size=384),
            settings=small_settings,
        )

        assert result == ReindexResult(documents=2, resized_from=768)
        assert (await db_session.execute(text(COLUMN_DIMENSION))).scalar_one() == 384
        await _assert_reindexed(db_session, ids, "nomic-embed-text")
    finally:
        await db_session.rollback()  # release read locks before the DDL
        async with sessionmaker() as session, session.begin():
            await session.execute(text("DELETE FROM document_chunks"))
            await resize_embedding_column(session, 768)

    db_session.expire_all()
    assert (await db_session.execute(text(COLUMN_DIMENSION))).scalar_one() == 768
    index = await db_session.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_document_chunks_embedding'")
    )
    assert index.scalar_one() == 1
