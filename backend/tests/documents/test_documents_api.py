"""Tests for the documents API."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.config import Settings
from app.documents.models import Document, DocumentChunk
from app.documents.service import clean_filename
from tests.conftest import register_user

TEXT = b"The quick brown fox jumps over the lazy dog. " * 20


async def upload(
    client: httpx.AsyncClient, headers: dict[str, str], name: str, content: bytes
) -> httpx.Response:
    return await client.post("/api/documents", headers=headers, files={"file": (name, content)})


async def test_upload_txt_becomes_ready(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    response = await upload(client, headers, "notes.txt", TEXT)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "processing"
    assert body["filename"] == "notes.txt"
    assert body["content_type"] == "text/plain"
    assert body["size_bytes"] == len(TEXT)

    got = await client.get(f"/api/documents/{body['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["status"] == "ready"
    assert got.json()["chunk_count"] >= 1


async def test_unsupported_extension_is_415(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    response = await upload(client, headers, "run.exe", b"MZ\x90\x00")
    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported file type. Upload a PDF, DOCX, TXT or MD file."


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("fake.pdf", b"MZ\x90\x00 not a pdf"),
        ("fake.docx", b"%PDF-1.4"),
        ("fake.txt", b"hello\x00world"),
    ],
)
async def test_content_not_matching_extension_is_415(
    client: httpx.AsyncClient, name: str, content: bytes
) -> None:
    headers = await register_user(client)
    response = await upload(client, headers, name, content)
    assert response.status_code == 415
    assert response.json()["detail"] == "The file's contents don't match its extension."


async def test_too_large_is_413_without_reading_past_limit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await register_user(client)
    reads: list[int] = []
    original = UploadFile.read

    async def spy(self: UploadFile, size: int = -1) -> bytes:
        piece = await original(self, size)
        reads.append(len(piece))
        return piece

    monkeypatch.setattr(UploadFile, "read", spy)
    response = await upload(client, headers, "big.txt", b"a" * (12 * 1024 * 1024))
    assert response.status_code == 413
    assert response.json()["detail"] == "File is larger than 10 MB."
    # Stopped at the piece that crossed 10 MB; the last MB of the 12 MB body stayed unread.
    assert len(reads) == 11
    assert sum(reads) == 11 * 1024 * 1024


async def test_empty_file_is_400(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    response = await upload(client, headers, "empty.txt", b"")
    assert response.status_code == 400
    assert response.json()["detail"] == "The file is empty."


async def test_list_is_newest_first(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    for name in ("a.txt", "b.txt", "c.txt"):
        assert (await upload(client, headers, name, TEXT)).status_code == 201
    listed = await client.get("/api/documents", headers=headers)
    assert [d["filename"] for d in listed.json()] == ["c.txt", "b.txt", "a.txt"]


async def test_other_users_cannot_see_or_delete(client: httpx.AsyncClient) -> None:
    alice = await register_user(client, email="alice@example.com")
    bob = await register_user(client, email="bob@example.com")
    doc_id = (await upload(client, alice, "mine.txt", TEXT)).json()["id"]

    assert (await client.get(f"/api/documents/{doc_id}", headers=bob)).status_code == 404
    deleted = await client.delete(f"/api/documents/{doc_id}", headers=bob)
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "Document not found"
    assert (await client.get("/api/documents", headers=bob)).json() == []
    assert (await client.get(f"/api/documents/{doc_id}", headers=alice)).status_code == 200


async def test_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/documents")).status_code == 401


async def test_delete_removes_rows_and_file(
    client: httpx.AsyncClient, db_session: AsyncSession, test_settings: Settings
) -> None:
    headers = await register_user(client)
    doc_id = (await upload(client, headers, "gone.txt", TEXT)).json()["id"]
    upload_dir = Path(test_settings.upload_dir)
    assert len([p for p in upload_dir.rglob("*") if p.is_file()]) == 1
    assert (await db_session.scalar(select(func.count()).select_from(DocumentChunk))) or 0

    response = await client.delete(f"/api/documents/{doc_id}", headers=headers)
    assert response.status_code == 204
    assert (await client.get(f"/api/documents/{doc_id}", headers=headers)).status_code == 404
    assert not [p for p in upload_dir.rglob("*") if p.is_file()]
    assert await db_session.scalar(select(func.count()).select_from(Document)) == 0
    assert await db_session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


async def test_filename_is_basename_only(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    response = await upload(client, headers, "../../evil.txt", TEXT)
    assert response.status_code == 201
    assert response.json()["filename"] == "evil.txt"


def test_clean_filename_drops_control_characters() -> None:
    assert clean_filename("a\x00b\n\r\t\x1b.txt") == "ab.txt"
    assert clean_filename("\x00\n") == "document"


async def test_control_characters_in_filename_are_dropped(client: httpx.AsyncClient) -> None:
    # httpx percent-encodes control characters in filenames, so send raw bytes.
    headers = await register_user(client)
    boundary = "testboundary"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a\x00b\n.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + TEXT
        + f"\r\n--{boundary}--\r\n".encode()
    )
    response = await client.post(
        "/api/documents",
        headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        content=body,
    )
    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "ab.txt"
