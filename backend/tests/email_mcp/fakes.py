"""A scripted stand-in for ``EmailServerClient``."""

from typing import Any


class FakeEmailClient:
    """Records ``(tool, arguments)`` calls and returns scripted results per tool name.

    A scripted value that is an exception instance is raised instead of returned.
    """

    def __init__(self, results: dict[str, Any] | None = None, *, ping_ok: bool = True) -> None:
        self.results: dict[str, Any] = results if results is not None else {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ping_ok = ping_ok

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        result = self.results.get(tool)
        if isinstance(result, BaseException):
            raise result
        return result

    async def ping(self) -> bool:
        return self.ping_ok
