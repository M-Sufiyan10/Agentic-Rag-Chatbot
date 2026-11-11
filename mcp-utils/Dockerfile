FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mcp_utils ./mcp_utils
RUN useradd --create-home app && chown -R app /app
USER app

ENV MCP_UTILS_HOST=0.0.0.0 \
    MCP_UTILS_PORT=8100
EXPOSE 8100
CMD ["uv","run","--no-dev","python","-m","mcp_utils"]
