import io
import uuid
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.config import Settings
from app.documents.embeddings import build_embeddings
from app.documents.ingestion import fail_interrupted_documents, ingest_document
from app.documents.models import Document, DocumentChunk
from app.documents.storage import LocalFileStorage

RESTART_MESSAGE = "Processing was interrupted by a server restart. Delete and upload it again."


class FailingEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("ollama down")

    def embed_query(self, text: str) -> list[float]:
        raise ConnectionError("ollama down")


@pytest.fixture
def sessionmaker(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def _user(db: AsyncSession) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.commit()
    return user


async def _document(
    db: AsyncSession,
    storage: LocalFileStorage,
    filename: str,
    data: bytes,
    status: str = "processing",
) -> Document:
    user = await _user(db)
    doc_id = uuid.uuid4()
    key = f"{user.id}/{doc_id}{Path(filename).suffix}"
    await storage.save(key, data)
    doc = Document(
        id=doc_id,
        user_id=user.id,
        filename=filename,
        content_type="application/octet-stream",
        size_bytes=len(data),
        storage_key=key,
        status=status,
    )
    db.add(doc)
    await db.commit()
    return doc


async def _chunks(db: AsyncSession, doc_id: uuid.UUID) -> list[DocumentChunk]:
    result = await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_id))
    return list(result.scalars())


async def _refreshed(db: AsyncSession, doc: Document) -> Document:
    await db.refresh(doc)
    return doc


async def _ingest(
    doc: Document,
    sm: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    embeddings: Embeddings,
    settings: Settings,
) -> None:
    await ingest_document(
        doc.id, sessionmaker=sm, storage=storage, embeddings=embeddings, settings=settings
    )


async def test_text_document_becomes_ready(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path)
    text = ("The quick brown fox jumps over the lazy dog. " * 56).encode()  # ~2500 chars
    doc = await _document(db_session, storage, "a.txt", text)
    await _ingest(doc, sessionmaker, storage, DeterministicFakeEmbedding(size=768), test_settings)

    doc = await _refreshed(db_session, doc)
    chunks = await _chunks(db_session, doc.id)
    assert doc.status == "ready"
    assert doc.error is None
    assert doc.chunk_count == len(chunks) > 1
    assert {c.embedding_model for c in chunks} == {"nomic-embed-text"}
    assert all(len(c.embedding) == 768 for c in chunks)


async def test_blank_pdf_fails_as_scanned(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    storage = LocalFileStorage(tmp_path)
    doc = await _document(db_session, storage, "b.pdf", buffer.getvalue())
    await _ingest(doc, sessionmaker, storage, DeterministicFakeEmbedding(size=768), test_settings)

    doc = await _refreshed(db_session, doc)
    assert doc.status == "failed"
    assert doc.error is not None and "scanned PDFs" in doc.error
    assert await _chunks(db_session, doc.id) == []


async def test_embedding_error_fails_without_chunks(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path)
    doc = await _document(db_session, storage, "a.txt", b"some text")
    await _ingest(doc, sessionmaker, storage, FailingEmbeddings(), test_settings)

    doc = await _refreshed(db_session, doc)
    assert doc.status == "failed"
    assert doc.error is not None and "embedding model may be unavailable" in doc.error
    assert await _chunks(db_session, doc.id) == []


async def test_deleted_document_is_a_noop(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path)
    doc = await _document(db_session, storage, "a.txt", b"some text")
    await db_session.delete(doc)
    await db_session.commit()

    await _ingest(doc, sessionmaker, storage, DeterministicFakeEmbedding(size=768), test_settings)

    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert await db_session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


async def test_fail_interrupted_documents(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path)
    p1 = await _document(db_session, storage, "a.txt", b"x")
    p2 = await _document(db_session, storage, "b.txt", b"x")
    ready = await _document(db_session, storage, "c.txt", b"x", status="ready")

    assert await fail_interrupted_documents(sessionmaker) == 2

    for doc in (p1, p2):
        await db_session.refresh(doc)
        assert (doc.status, doc.error) == ("failed", RESTART_MESSAGE)
    await db_session.refresh(ready)
    assert ready.status == "ready"


def test_unsupported_embedding_provider_raises(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"embedding_provider": "nope"})
    with pytest.raises(ValueError, match="nope"):
        build_embeddings(settings)


class SpyEmbeddings(DeterministicFakeEmbedding):
    """Fake embeddings that count document-embedding calls."""

    calls: int = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed_documents(texts)


async def test_document_over_chunk_limit_fails_without_embedding(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(update={"max_document_chunks": 2})
    storage = LocalFileStorage(tmp_path)
    text = ("The quick brown fox jumps over the lazy dog. " * 56).encode()  # ~2500 chars
    doc = await _document(db_session, storage, "long.txt", text)
    spy = SpyEmbeddings(size=768)
    await _ingest(doc, sessionmaker, storage, spy, settings)

    doc = await _refreshed(db_session, doc)
    assert doc.status == "failed"
    assert doc.error == (
        "This document is too long to index (3 passages; the limit is 2). "
        "Split it into smaller files."
    )
    assert doc.chunk_count == 0
    assert await _chunks(db_session, doc.id) == []
    assert spy.calls == 0
