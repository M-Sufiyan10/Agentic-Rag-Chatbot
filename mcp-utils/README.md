# mcp-utils

A small MCP server (Python `mcp` SDK, `MCPServer`/FastMCP API) with three keyless tools:
`get_weather` (Open-Meteo), `convert_units` (pint) and `convert_time` (zoneinfo).

```bash
uv sync
uv run python -m mcp_utils          # streamable HTTP at http://127.0.0.1:8100/mcp
uv run python -m mcp_utils --stdio  # stdio, for local debugging
```

Environment: `MCP_UTILS_HOST` (default `127.0.0.1`), `MCP_UTILS_PORT` (default `8100`).

Checks: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .`
