"""The ``search_documents`` tool: semantic search over the current user's documents.

The user is never a tool argument. ``ToolNode`` injects the run config, and
``user_id`` is read from ``config["configurable"]["user_id"]``, which
``app.chat.streaming.stream_agent_events`` sets from the authenticated user. A
``user_id`` the model puts in its tool call is not part of the schema and is
ignored.
"""

import logging
import uuid
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.documents.models import Document
from app.documents.retrieval import RetrievedChunk, count_mismatched_chunks, search_chunks

logger = logging.getLogger(__name__)

SNIPPET_LENGTH = 300
UNAVAILABLE = "Error: document search is unavailable in this context."
SEARCH_FAILED = "Error: document search failed. Answer without the documents and say so."
STILL_PROCESSING = "Your documents are still being processed. Try again in a moment."
NO_READY_DOCUMENTS = "You have no ready documents to search. Upload one from the Documents panel."
NOTHING_FOUND = "No matching passages were found in your documents."
MISMATCH_NOTE = (
    "Note: {n} chunks were indexed with a different embedding model and were skipped; "
    "ask the administrator to run the reindex command."
)

Citation = dict[str, Any]


def snippet(text: str) -> str:
    """Collapse whitespace and truncate to ``SNIPPET_LENGTH`` characters."""
    return " ".join(text.split())[:SNIPPET_LENGTH]


def _citation(chunk: RetrievedChunk) -> Citation:
    return {
        "document_id": str(chunk.document_id),
        "filename": chunk.filename,
        "chunk_index": chunk.chunk_index,
        "snippet": snippet(chunk.content),
    }


async def _count_documents(session: AsyncSession, user_id: uuid.UUID, status: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.user_id == user_id, Document.status == status)
    )
    return count or 0


def make_search_documents_tool(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    settings: Settings,
) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def search_documents(query: str, config: RunnableConfig) -> tuple[str, list[Citation]]:
        """Search the user's uploaded documents for passages relevant to the query.

        Use this when the user mentions their documents, files or uploads, or asks
        something their documents likely cover. Returns numbered passages; cite them
        with their bracket numbers, e.g. [1].
        """
        raw_user_id = config.get("configurable", {}).get("user_id")
        if raw_user_id is None:
            return UNAVAILABLE, []
        try:
            user_id = uuid.UUID(str(raw_user_id))
        except ValueError:
            return UNAVAILABLE, []

        try:
            query_vector = await embeddings.aembed_query(query)
            async with sessionmaker() as session:
                chunks = await search_chunks(
                    session,
                    user_id=user_id,
                    query_vector=query_vector,
                    embedding_model=settings.embedding_model,
                    k=settings.retrieval_k,
                )
                mismatched = await count_mismatched_chunks(
                    session, user_id=user_id, embedding_model=settings.embedding_model
                )
                if chunks:
                    processing = ready = 0
                else:
                    processing = await _count_documents(session, user_id, "processing")
                    ready = await _count_documents(session, user_id, "ready")
        except Exception:
            logger.exception("Document search failed")
            return SEARCH_FAILED, []

        if chunks:
            content = "\n\n".join(
                f"[{n}] {chunk.filename} (chunk {chunk.chunk_index})\n{chunk.content}"
                for n, chunk in enumerate(chunks, start=1)
            )
        elif processing:
            content = STILL_PROCESSING
        elif not ready:
            content = NO_READY_DOCUMENTS
        else:
            content = NOTHING_FOUND
        if mismatched:
            content += "\n\n" + MISMATCH_NOTE.format(n=mismatched)
        return content, [_citation(chunk) for chunk in chunks]

    return search_documents
