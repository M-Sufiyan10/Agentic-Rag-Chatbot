"""A tiny stdio MCP server for the backend's MCP client tests."""

import asyncio

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo")


@server.tool()
def shout(text: str) -> str:
    """Return the text in upper case."""
    return text.upper()


@server.tool()
def boom() -> str:
    """Always fail."""
    raise RuntimeError("kaboom")


@server.tool()
async def nap(seconds: float) -> str:
    """Sleep for the given number of seconds, then answer."""
    await asyncio.sleep(seconds)
    return "awake"


if __name__ == "__main__":
    server.run(transport="stdio")
