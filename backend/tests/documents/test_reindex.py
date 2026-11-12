import io
import sys
import uuid
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from pypdf import PdfWriter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.config import Settings
from app.documents import reindex as reindex_module
from app.documents.ingestion import EMBEDDING_FAILED, ingest_document
from app.documents.models import Document, DocumentChunk
from app.documents.reindex import (
    FailedDocument,
    ReindexResult,
    format_summary,
    reindex,
    resize_embedding_column,
)
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


class FailingEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("ollama down")

    def embed_query(self, text: str) -> list[float]:
        raise ConnectionError("ollama down")


async def test_reindex_counts_documents_that_fail_to_embed(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    embeddings: Embeddings,
    tmp_path: Path,
) -> None:
    storage, _ = await _setup(db_session, tmp_path, sessionmaker, test_settings, embeddings)

    result = await reindex(
        sessionmaker=sessionmaker,
        storage=storage,
        embeddings=FailingEmbeddings(),
        settings=test_settings,
    )

    embedding_failed = (
        FailedDocument("a.txt", EMBEDDING_FAILED),
        FailedDocument("b.txt", EMBEDDING_FAILED),
    )
    assert result == ReindexResult(documents=2, resized_from=None, failed=embedding_failed)
    db_session.expire_all()
    docs = list((await db_session.execute(select(Document))).scalars())
    assert {d.status for d in docs} == {"failed"}


def test_summary_lists_failures(test_settings: Settings) -> None:
    ok = ReindexResult(documents=2, resized_from=None)
    assert format_summary(ok, test_settings) == "Reindexed 2 documents with nomic-embed-text"
    bad = ReindexResult(
        documents=3,
        resized_from=None,
        failed=(FailedDocument("a.txt", "embedding down"),),
        unreadable=(FailedDocument("scan.pdf", "no text"),),
    )
    assert format_summary(bad, test_settings) == (
        "Reindexed 3 documents with nomic-embed-text (1 failed) "
        "(1 could not be indexed from their files)\n"
        "Failed (fix the cause and run reindex again):\n"
        "  - a.txt: embedding down\n"
        "Could not be indexed (not an error; the user can replace these):\n"
        "  - scan.pdf: no text"
    )


async def _add_blank_pdf(db: AsyncSession, storage: LocalFileStorage, user_id: uuid.UUID) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    doc_id = uuid.uuid4()
    key = f"{user_id}/{doc_id}.pdf"
    await storage.save(key, buffer.getvalue())
    db.add(
        Document(
            id=doc_id,
            user_id=user_id,
            filename="scan.pdf",
            content_type="application/pdf",
            size_bytes=1,
            storage_key=key,
            status="failed",
            error="whatever it said before",
        )
    )
    await db.commit()


@pytest.fixture
def cli_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point the command's ``_main`` at the test database and upload directory."""
    monkeypatch.setattr(reindex_module, "get_settings", lambda: test_settings)
    return test_settings


async def test_cli_reports_unreadable_files_but_exits_zero(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    cli_settings: Settings,
    embeddings: Embeddings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage, _ = await _setup(
        db_session, Path(cli_settings.upload_dir), sessionmaker, cli_settings, embeddings
    )
    user_id = (await db_session.execute(select(User.id))).scalar_one()
    await _add_blank_pdf(db_session, storage, user_id)
    await db_session.rollback()  # the command needs the table lock for DELETE
    monkeypatch.setattr(reindex_module, "build_embeddings", lambda settings: embeddings)

    exit_code = await reindex_module._main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Reindexed 3 documents with nomic-embed-text (1 could not be indexed" in out
    assert "  - scan.pdf: No text could be extracted" in out
    assert "Failed (" not in out


async def test_cli_exits_non_zero_when_embedding_fails(
    db_session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    cli_settings: Settings,
    embeddings: Embeddings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _setup(db_session, Path(cli_settings.upload_dir), sessionmaker, cli_settings, embeddings)
    await db_session.rollback()
    monkeypatch.setattr(reindex_module, "build_embeddings", lambda settings: FailingEmbeddings())

    exit_code = await reindex_module._main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "(2 failed)" in out
    assert f"  - a.txt: {EMBEDDING_FAILED}" in out
    assert f"  - b.txt: {EMBEDDING_FAILED}" in out


def test_cli_module_registers_every_table_on_its_own() -> None:
    """Run as ``python -m``, the command must see ``users`` or saving chunks fails.

    A fresh interpreter is needed: inside pytest the app has already imported every model.
    """
    import subprocess

    probe = (
        "import app.documents.reindex\n"
        "from app.documents.models import Document\n"
        "assert 'users' in Document.metadata.tables, sorted(Document.metadata.tables)\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
