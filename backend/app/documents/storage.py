"""File storage for uploaded documents."""

import asyncio
from pathlib import Path
from typing import Protocol


class FileStorage(Protocol):
    async def save(self, key: str, data: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None:
        """Delete ``key``; a missing key is not an error."""
        ...


class LocalFileStorage:
    """Stores files on local disk under ``base_dir``."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()

    def _path(self, key: str) -> Path:
        path = (self._base / key).resolve()
        if not path.is_relative_to(self._base) or path == self._base:
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    async def save(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)
