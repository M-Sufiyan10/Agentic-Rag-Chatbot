"""Per-user vector search over document chunks.

Every query here filters by ``user_id``: one user's chunks are never visible to
another (the project's first anti-goal). ``user_id`` must come from the server
(the authenticated user), never from model or client input.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
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
    """
    distance = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")
    rows = await session.execute(
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
    )
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
