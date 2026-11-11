"""Per-user vector search over document chunks.

Every query here filters by ``user_id``: one user's chunks are never visible to
another (the project's first anti-goal). ``user_id`` must come from the server
(the authenticated user), never from model or client input.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.models import Document, DocumentChunk


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    distance: float


async def search_chunks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query_vector: list[float],
    embedding_model: str,
    k: int,
) -> list[RetrievedChunk]:
    """Return the user's ``k`` nearest chunks by cosine distance, closest first.

    Only chunks embedded with ``embedding_model`` (vectors from another model
    are not comparable) and belonging to ``ready`` documents are searched.

    The HNSW index scan returns ``hnsw.ef_search`` (default 40) candidates from
    the *whole* table, and the ``user_id`` filter is applied afterwards, so a
    user with few chunks among many other users' would get fewer than ``k``
    results, or none. pgvector's iterative scan keeps scanning until ``k`` rows
    pass the filter. Its ``relaxed_order`` mode may return them slightly out of
    order, so the outer query sorts them again (``+ 0`` stops PostgreSQL < 17
    from assuming the materialized CTE is already sorted). ``SET LOCAL`` lasts
    until the end of the current transaction.
    """
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")
    nearest = (
        select(
            DocumentChunk.document_id,
            Document.filename,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.user_id == user_id,
            Document.user_id == user_id,
            DocumentChunk.embedding_model == embedding_model,
            Document.status == "ready",
        )
        .order_by(distance)
        .limit(k)
        .cte("nearest")
        .prefix_with("MATERIALIZED")
    )
    rows = await session.execute(select(nearest).order_by(nearest.c.distance + 0))
    return [
        RetrievedChunk(
            document_id=row.document_id,
            filename=row.filename,
            chunk_index=row.chunk_index,
            content=row.content,
            distance=float(row.distance),
        )
        for row in rows
    ]


async def count_mismatched_chunks(
    session: AsyncSession, *, user_id: uuid.UUID, embedding_model: str
) -> int:
    """Count the user's chunks embedded with a model other than ``embedding_model``."""
    count = await session.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(
            DocumentChunk.user_id == user_id,
            DocumentChunk.embedding_model != embedding_model,
        )
    )
    return count or 0


async def count_documents(session: AsyncSession, *, user_id: uuid.UUID, status: str) -> int:
    """Count the user's documents with ``status``."""
    count = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.user_id == user_id, Document.status == status)
    )
    return count or 0


@dataclass(frozen=True)
class DocumentOverview:
    """What the agent is told about the user's documents at the start of a turn."""

    ready: list[str]  # filenames of ready documents, newest first, at most ``limit``
    ready_total: int
    processing: int


async def document_overview(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int
) -> DocumentOverview:
    """The user's ready filenames (newest first, at most ``limit``) and status counts."""
    ready = await session.scalars(
        select(Document.filename)
        .where(Document.user_id == user_id, Document.status == "ready")
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    return DocumentOverview(
        ready=list(ready),
        ready_total=await count_documents(session, user_id=user_id, status="ready"),
        processing=await count_documents(session, user_id=user_id, status="processing"),
    )
