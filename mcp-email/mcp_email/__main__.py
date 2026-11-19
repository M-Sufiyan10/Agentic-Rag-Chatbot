"""Run the server: authenticated streamable HTTP by default, `--stdio` for local debugging."""

import sys
from os import getenv

import uvicorn

from mcp_email.auth import BearerTokenMiddleware
from mcp_email.server import mcp


def main() -> None:
    if "--stdio" in sys.argv[1:]:
        mcp.run(transport="stdio")
        return
    token = getenv("MCP_EMAIL_TOKEN", "")
    if not token:
        print("MCP_EMAIL_TOKEN must be set to a non-empty value.", file=sys.stderr)
        raise SystemExit(2)
    host = getenv("MCP_EMAIL_HOST", "127.0.0.1")
    port = int(getenv("MCP_EMAIL_PORT", "8101"))
    app = BearerTokenMiddleware(
        mcp.streamable_http_app(streamable_http_path="/mcp", host=host), token
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
