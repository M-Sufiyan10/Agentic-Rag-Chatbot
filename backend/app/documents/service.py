"""Document data access and upload validation. Every query is scoped to the owning user."""

import unicodedata
import uuid
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.extraction import SUPPORTED_EXTENSIONS
from app.documents.models import Document
from app.documents.storage import FileStorage

_READ_CHUNK = 1024 * 1024
_SNIFF_BYTES = 8192


def clean_filename(raw: str | None) -> str:
    """Basename only (either path separator), no control characters, at most 255 characters.

    Control characters (NUL, newlines, ...) are dropped: Postgres rejects NUL in
    text, and filenames are shown to the model in its system prompt.
    """
    printable = "".join(ch for ch in (raw or "") if unicodedata.category(ch) != "Cc")
    name = PurePosixPath(printable.replace("\\", "/")).name
    return (name or "document")[:255]


def extension_of(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


async def read_limited(file: UploadFile, max_bytes: int, max_mb: int) -> bytes:
    """Read the upload in 1 MB pieces, stopping as soon as it exceeds the limit (413)."""
    chunks: list[bytes] = []
    total = 0
    while piece := await file.read(_READ_CHUNK):
        total += len(piece)
        if total > max_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"File is larger than {max_mb} MB."
            )
        chunks.append(piece)
    return b"".join(chunks)


def check_contents_match_extension(ext: str, data: bytes) -> None:
    if ext == ".pdf":
        ok = data.startswith(b"%PDF-")
    elif ext == ".docx":
        ok = data.startswith(b"PK\x03\x04")
    else:
        ok = b"\x00" not in data[:_SNIFF_BYTES]
    if not ok:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "The file's contents don't match its extension.",
        )


async def get_owned_document(session: AsyncSession, user_id: UUID, document_id: UUID) -> Document:
    """Return the user's document, or raise 404 (also for other users' ids)."""
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


async def list_documents(session: AsyncSession, user_id: UUID) -> list[Document]:
    result = await session.scalars(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return list(result)


async def create_document(
    session: AsyncSession,
    storage: FileStorage,
    user_id: UUID,
    filename: str,
    data: bytes,
) -> Document:
    """Store the file and insert the row (status ``processing``)."""
    ext = extension_of(filename)
    document_id = uuid.uuid4()
    key = f"{user_id}/{document_id}{ext}"
    await storage.save(key, data)
    document = Document(
        id=document_id,
        user_id=user_id,
        filename=filename,
        content_type=SUPPORTED_EXTENSIONS[ext],
        size_bytes=len(data),
        storage_key=key,
    )
    session.add(document)
    try:
        await session.commit()
    except Exception:
        await storage.delete(key)
        raise
    await session.refresh(document)
    return document


async def delete_document(session: AsyncSession, storage: FileStorage, document: Document) -> None:
    """Delete the row (chunks cascade), then the stored file."""
    key = document.storage_key
    await session.delete(document)
    await session.commit()
    await storage.delete(key)
