"""Re-embed every document with the configured embedding model.

Run this after changing ``EMBEDDING_MODEL`` or ``EMBEDDING_DIMENSIONS``::

    uv run python -m app.documents.reindex

Chunks remember which model embedded them, and search only uses chunks from
the configured model, so after a model change old documents are invisible to
search until they are re-embedded. If the dimension changed too, the vector
column and its HNSW index are rebuilt at the new size first (which discards
every existing chunk, since vectors of different sizes can't be converted).
"""

import asyncio
import sys
from typing import NamedTuple

from langchain_core.embeddings import Embeddings
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker
from app.documents.embeddings import build_embeddings
from app.documents.ingestion import ingest_document
from app.documents.models import Document
from app.documents.storage import FileStorage, LocalFileStorage

COLUMN_DIMENSION_SQL = (
    "SELECT atttypmod FROM pg_attribute "
    "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
)


class ReindexResult(NamedTuple):
    documents: int
    resized_from: int | None


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
    return ReindexResult(documents=len(document_ids), resized_from=resized_from)


async def _main() -> None:
    settings = get_settings()
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
    summary = f"Reindexed {result.documents} documents with {settings.embedding_model}"
    if result.resized_from is not None:
        summary += f" (resized vectors {result.resized_from} → {settings.embedding_dimensions})"
    print(summary)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as exc:
        print(f"Reindex failed: {exc}", file=sys.stderr)
        sys.exit(1)
