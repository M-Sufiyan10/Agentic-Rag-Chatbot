"""Background ingestion: extract, chunk, embed and store a document."""

import asyncio
import logging
import uuid

from langchain_core.embeddings import Embeddings
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.documents.extraction import ExtractionError, chunk_pages, extract_text
from app.documents.models import Document, DocumentChunk
from app.documents.storage import FileStorage

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32
EMBEDDING_FAILED = (
    "Couldn't process this document: the embedding model may be unavailable. "
    "Delete it and try again."
)
TOO_LONG = (
    "This document is too long to index ({chunks} passages; the limit is {limit}). "
    "Split it into smaller files."
)
INTERRUPTED = "Processing was interrupted by a server restart. Delete and upload it again."


async def _mark_failed(
    sessionmaker: async_sessionmaker[AsyncSession], document_id: uuid.UUID, message: str
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status="failed", error=message)
        )
        await session.commit()


async def _embed(embeddings: Embeddings, chunks: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        vectors.extend(await embeddings.aembed_documents(chunks[start : start + EMBED_BATCH_SIZE]))
    return vectors


async def ingest_document(
    document_id: uuid.UUID,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: FileStorage,
    embeddings: Embeddings,
    settings: Settings,
) -> None:
    """Ingest one document. Never raises; failures are recorded on the row."""
    try:
        async with sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return  # deleted before ingestion started
            user_id, filename, key = document.user_id, document.filename, document.storage_key

        try:
            data = await storage.read(key)
            # Parsing and chunking are CPU-bound (seconds for a large file); run them in a
            # worker thread so the event loop keeps serving other requests meanwhile.
            pages = await asyncio.to_thread(extract_text, filename, data)
            chunks = await asyncio.to_thread(
                chunk_pages, pages, settings.chunk_size, settings.chunk_overlap
            )
            if len(chunks) > settings.max_document_chunks:
                # Embedding is the slow part (about a second per chunk on a CPU), so a
                # huge file would tie up the embedding server for hours.
                raise ExtractionError(
                    TOO_LONG.format(chunks=len(chunks), limit=settings.max_document_chunks)
                )
            vectors = await _embed(embeddings, chunks)
        except ExtractionError as exc:
            await _mark_failed(sessionmaker, document_id, str(exc))
            return

        async with sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return  # deleted while embedding
            session.add_all(
                DocumentChunk(
                    document_id=document_id,
                    user_id=user_id,
                    chunk_index=index,
                    content=content,
                    embedding=vector,
                    embedding_model=settings.embedding_tag,
                )
                for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True))
            )
            document.status = "ready"
            document.chunk_count = len(chunks)
            document.error = None
            await session.commit()
    except Exception:
        logger.exception("Ingestion failed for document %s", document_id)
        try:
            await _mark_failed(sessionmaker, document_id, EMBEDDING_FAILED)
        except Exception:
            logger.exception("Could not record ingestion failure for document %s", document_id)


async def fail_interrupted_documents(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Mark documents left ``processing`` by a restart as failed; return how many."""
    async with sessionmaker() as session:
        result = await session.execute(
            update(Document)
            .where(Document.status == "processing")
            .values(status="failed", error=INTERRUPTED)
            .returning(Document.id)
        )
        count = len(result.all())
        await session.commit()
        return count
