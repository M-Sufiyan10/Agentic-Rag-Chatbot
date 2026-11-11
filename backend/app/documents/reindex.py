"""Re-embed every document with the configured embedding model.

Run this after changing ``EMBEDDING_MODEL`` or ``EMBEDDING_DIMENSIONS``::

    uv run python -m app.documents.reindex

Chunks remember which model embedded them, and search only uses chunks from
the configured model, so after a model change old documents are invisible to
search until they are re-embedded. If the dimension changed too, the vector
column and its HNSW index are rebuilt at the new size first (which discards
every existing chunk, since vectors of different sizes can't be converted).

Documents that fail to re-embed (for example because the embedding server is
down) are marked ``failed``, listed in the summary, and make the command exit
non-zero. Documents whose files can't be indexed (a scanned PDF, a file over
``MAX_DOCUMENT_CHUNKS``) are also marked ``failed`` and listed, but don't count
as an error: running the command again would not change them. If the run is
interrupted part way, documents are left ``processing`` with their chunks
removed; the original files are still in storage, so simply running the
command again recovers every document.
"""

import asyncio
import sys
import uuid
from typing import NamedTuple

from langchain_core.embeddings import Embeddings
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker
from app.documents.embeddings import build_embeddings
from app.documents.ingestion import EMBEDDING_FAILED, ingest_document
from app.documents.models import Document
from app.documents.storage import FileStorage, LocalFileStorage

COLUMN_DIMENSION_SQL = (
    "SELECT atttypmod FROM pg_attribute "
    "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
)


class FailedDocument(NamedTuple):
    filename: str
    error: str


class ReindexResult(NamedTuple):
    documents: int
    resized_from: int | None
    failed: tuple[FailedDocument, ...] = ()  # embedding (or other) errors: exit non-zero
    unreadable: tuple[FailedDocument, ...] = ()  # the file itself can't be indexed


async def resize_embedding_column(session: AsyncSession, dimensions: int) -> None:
    """Change the embedding column's size and rebuild its HNSW index.

    The table must be empty. The caller owns the transaction.
    """
    await session.execute(text("DROP INDEX IF EXISTS ix_document_chunks_embedding"))
    await session.execute(
        text(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({int(dimensions)})")
    )
    await session.execute(
        text(
            "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    )


async def reindex(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: FileStorage,
    embeddings: Embeddings,
    settings: Settings,
) -> ReindexResult:
    """Re-embed all documents, resizing the vector column first if needed."""
    async with sessionmaker() as session, session.begin():
        current = (await session.execute(text(COLUMN_DIMENSION_SQL))).scalar_one()
        resized_from = current if current != settings.embedding_dimensions else None
        # Ingestion only inserts, so old chunks must go first in every case.
        await session.execute(text("DELETE FROM document_chunks"))
        if resized_from is not None:
            await resize_embedding_column(session, settings.embedding_dimensions)
        result = await session.execute(
            update(Document)
            .values(status="processing", error=None, chunk_count=0)
            .returning(Document.id)
        )
        document_ids = list(result.scalars())

    for document_id in document_ids:
        await ingest_document(
            document_id,
            sessionmaker=sessionmaker,
            storage=storage,
            embeddings=embeddings,
            settings=settings,
        )
    failed, unreadable = await _failures(sessionmaker, document_ids)
    return ReindexResult(
        documents=len(document_ids),
        resized_from=resized_from,
        failed=failed,
        unreadable=unreadable,
    )


async def _failures(
    sessionmaker: async_sessionmaker[AsyncSession], document_ids: list[uuid.UUID]
) -> tuple[tuple[FailedDocument, ...], tuple[FailedDocument, ...]]:
    """Split this run's documents that didn't become ``ready`` into (failed, unreadable).

    Only ``document_ids`` count: a document uploaded during the run is not ours to
    report. Ingestion records an ``ExtractionError``'s message for a file that can't
    be indexed and ``EMBEDDING_FAILED`` for everything else.
    """
    async with sessionmaker() as session:
        rows = await session.execute(
            select(Document.filename, Document.status, Document.error)
            .where(Document.id.in_(document_ids), Document.status != "ready")
            .order_by(Document.filename)
        )
    failed: list[FailedDocument] = []
    unreadable: list[FailedDocument] = []
    for filename, status, error in rows:
        if status == "failed" and error and error != EMBEDDING_FAILED:
            unreadable.append(FailedDocument(filename, error))
        else:
            failed.append(FailedDocument(filename, error or f"still {status}"))
    return tuple(failed), tuple(unreadable)


def format_summary(result: ReindexResult, settings: Settings) -> str:
    summary = f"Reindexed {result.documents} documents with {settings.embedding_model}"
    if result.resized_from is not None:
        summary += (
            f" (resized vectors {result.resized_from} \u2192 {settings.embedding_dimensions})"
        )
    if result.failed:
        summary += f" ({len(result.failed)} failed)"
    if result.unreadable:
        summary += f" ({len(result.unreadable)} could not be indexed from their files)"
    for title, documents in [
        ("Failed (fix the cause and run reindex again):", result.failed),
        ("Could not be indexed (not an error; the user can replace these):", result.unreadable),
    ]:
        if documents:
            summary += f"\n{title}"
            summary += "".join(f"\n  - {d.filename}: {d.error}" for d in documents)
    return summary


async def _main() -> int:
    settings = get_settings()
    print(
        "Reindexing all documents. If this is interrupted, run it again: "
        "documents are restored from their stored files."
    )
    engine = create_engine(settings.database_url)
    try:
        result = await reindex(
            sessionmaker=create_sessionmaker(engine),
            storage=LocalFileStorage(settings.upload_dir),
            embeddings=build_embeddings(settings),
            settings=settings,
        )
    finally:
        await engine.dispose()
    print(format_summary(result, settings))
    return 1 if result.failed else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(_main()))
    except Exception as exc:
        print(f"Reindex failed: {exc}", file=sys.stderr)
        sys.exit(1)
