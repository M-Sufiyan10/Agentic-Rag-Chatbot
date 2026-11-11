from pathlib import Path

import pytest

from app.documents.storage import LocalFileStorage


async def test_round_trip_and_delete(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    await storage.save("u1/doc.txt", b"hello")
    assert await storage.read("u1/doc.txt") == b"hello"
    await storage.delete("u1/doc.txt")
    with pytest.raises(FileNotFoundError):
        await storage.read("u1/doc.txt")


async def test_delete_missing_key_is_noop(tmp_path: Path) -> None:
    await LocalFileStorage(tmp_path).delete("nope/missing.txt")


async def test_escaping_key_rejected(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    for op in (
        storage.save("../escape", b"x"),
        storage.read("../escape"),
        storage.delete("../escape"),
    ):
        with pytest.raises(ValueError):
            await op
