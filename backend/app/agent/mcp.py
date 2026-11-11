"""Tools from MCP servers listed in a JSON config, isolated so a broken server can't break the app.

Each server is loaded on its own with a startup deadline; one that fails or is
too slow is skipped with a WARNING. A server entry may carry
``"tool_descriptions": {tool: text}`` to replace a tool's upstream description
(it is ours, not a connection key). Every kept tool is wrapped by
``guard_mcp_tool``, so a call that hangs or fails returns an ``"Error: …"``
string instead of raising. Tool calls stay stateless: the adapter opens a fresh
session for each call.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Transport -> keys it requires (all strings).
_REQUIRED_KEYS = {"streamable_http": ("url",), "stdio": ("command",)}


def load_mcp_config(path: Path) -> dict[str, dict[str, Any]]:
    """Read and validate the MCP server config; a missing file means no servers.

    Relative paths resolve against the backend directory, not the CWD. Raises
    ``ValueError`` naming the file when it isn't valid JSON or an entry has an
    unknown transport, a missing required key, non-string args or
    ``tool_descriptions`` that isn't an object of strings.
    """
    if not path.is_absolute():
        path = BACKEND_DIR / path
    if not path.exists():
        logger.info("No MCP config at %s; no MCP servers", path)
        return {}

    def invalid(reason: str) -> ValueError:
        return ValueError(f"Invalid MCP config {path}: {reason}")

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise invalid(f"not valid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise invalid("expected an object mapping server names to connections")

    config: dict[str, dict[str, Any]] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise invalid(f"server {name!r} must be an object")
        transport = entry.get("transport")
        if transport not in _REQUIRED_KEYS:
            raise invalid(
                f"server {name!r} has unknown transport {transport!r}; "
                f"use one of {', '.join(_REQUIRED_KEYS)}"
            )
        for key in _REQUIRED_KEYS[transport]:
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise invalid(f"server {name!r} ({transport}) needs a string {key!r}")
        if transport == "stdio":
            args = entry.setdefault("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise invalid(f"server {name!r} 'args' must be a list of strings")
        descriptions = entry.get("tool_descriptions", {})
        if not isinstance(descriptions, dict) or not all(
            isinstance(text, str) for text in descriptions.values()
        ):
            raise invalid(f"server {name!r} 'tool_descriptions' must map tool names to strings")
        config[name] = entry
    return config


async def load_mcp_tools(
    connections: dict[str, dict[str, Any]],
    *,
    reserved_names: set[str],
    tool_timeout: float,
    startup_timeout: float,
) -> tuple[list[BaseTool], dict[str, str]]:
    """Load each server's tools, skipping unavailable servers and clashing tool names.

    Returns the guarded tools and each server's status (``"ok"`` or ``"unavailable"``).
    """
    tools: list[BaseTool] = []
    statuses: dict[str, str] = {}
    taken = set(reserved_names)
    for server, entry in connections.items():
        connection = dict(entry)
        descriptions: dict[str, str] = connection.pop("tool_descriptions", {})
        client = MultiServerMCPClient({server: connection})  # type: ignore[dict-item]
        try:
            server_tools = await asyncio.wait_for(client.get_tools(), startup_timeout)
        except Exception as exc:
            logger.warning("MCP server %r unavailable, skipping: %s", server, _reason(exc))
            statuses[server] = "unavailable"
            continue
        statuses[server] = "ok"
        for tool in server_tools:
            if tool.name in taken:
                logger.warning(
                    "MCP tool %r from server %r clashes with an existing tool name, skipping",
                    tool.name,
                    server,
                )
                continue
            taken.add(tool.name)
            tools.append(
                guard_mcp_tool(
                    tool,
                    server=server,
                    timeout=tool_timeout,
                    description=descriptions.get(tool.name),
                )
            )
    return tools, statuses


def _reason(exc: BaseException) -> str:
    """A readable reason: the first leaf of an exception group, or the exception's name."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return str(exc) or type(exc).__name__


def _content_text(content: Any) -> Any:
    """Plain text for text-only content blocks; anything else unchanged."""
    if isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") == "text" for block in content
    ):
        return "\n".join(str(block.get("text", "")) for block in content)
    return content


def guard_mcp_tool(
    tool: BaseTool, *, server: str, timeout: float, description: str | None = None
) -> BaseTool:
    """Wrap ``tool`` so a call that hangs or fails returns an ``"Error: …"`` string.

    ``description`` replaces the tool's own description when given.

    A tool error reported by the server (an MCP ``isError`` result) is already
    turned into content by the adapter and passes through as its text.
    """

    async def call(**kwargs: Any) -> Any:
        try:
            result = await asyncio.wait_for(tool.ainvoke(kwargs), timeout)
        except TimeoutError:
            return f"Error: the {server} tool server didn't respond in {timeout:g}s."
        except ToolException as exc:
            return str(exc)
        except Exception as exc:
            return f"Error: the {server} tool server failed ({_reason(exc)})."
        return _content_text(result)

    return StructuredTool(
        name=tool.name,
        description=tool.description if description is None else description,
        args_schema=tool.args_schema,
        coroutine=call,
    )
