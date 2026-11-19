"""Client for the email MCP server: one fresh, authenticated session per call.

The call goes straight to the MCP session without LangChain callbacks or config,
so tracing (Langfuse) never records the ``account`` argument. Error messages never
include the arguments, which carry the mailbox password.
"""

import asyncio
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.types import CallToolResult, TextContent


def _result_value(result: CallToolResult) -> Any:
    """The tool's value: structured content (unwrapping FastMCP's ``{"result": x}``), else text.

    MCP wraps a primitive return (``-> str``) as ``{"result": value}``; dict-returning
    tools come back as their own dict.
    """
    structured = result.structuredContent
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


class EmailServerError(Exception):
    """The email MCP server was unreachable, timed out, or returned an error."""


class EmailServerClient:
    def __init__(self, url: str, token: str, timeout: float) -> None:
        self._timeout = timeout
        self._client = MultiServerMCPClient(
            {
                "email": {
                    "transport": "streamable_http",
                    "url": url,
                    "headers": {"Authorization": f"Bearer {token}"},
                }
            }
        )

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Call ``tool``; return its structured dict if it has one, else its text."""

        async def run() -> Any:
            async with self._client.session("email") as session:
                result = await session.call_tool(tool, arguments)
            texts = [c.text for c in result.content if isinstance(c, TextContent)]
            if result.isError:
                raise EmailServerError(f"Email server error from {tool}: {' '.join(texts)[:300]}")
            return _result_value(result)

        try:
            return await asyncio.wait_for(run(), self._timeout)
        except EmailServerError:
            raise
        except TimeoutError as exc:
            raise EmailServerError(f"Email server timed out calling {tool}") from exc
        except Exception as exc:  # noqa: BLE001 - any transport failure becomes one error type
            raise EmailServerError(
                f"Could not reach the email server for {tool} ({type(exc).__name__})"
            ) from exc

    async def ping(self) -> bool:
        async def run() -> bool:
            async with self._client.session("email") as session:
                await session.list_tools()
            return True

        try:
            return await asyncio.wait_for(run(), self._timeout)
        except Exception:  # noqa: BLE001
            return False


__all__ = ["EmailServerClient", "EmailServerError"]
