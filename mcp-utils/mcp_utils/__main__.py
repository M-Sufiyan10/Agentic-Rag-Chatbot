"""Run the server: streamable HTTP by default, `--stdio` for local debugging."""

import sys
from os import getenv

from mcp_utils.server import mcp


def main() -> None:
    if "--stdio" in sys.argv[1:]:
        mcp.run(transport="stdio")
        return
    mcp.run(
        transport="streamable-http",
        host=getenv("MCP_UTILS_HOST", "127.0.0.1"),
        port=int(getenv("MCP_UTILS_PORT", "8100")),
        streamable_http_path="/mcp",
    )


if __name__ == "__main__":
    main()
