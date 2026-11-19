FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mcp_email ./mcp_email
RUN useradd --create-home app && chown -R app /app
USER app

ENV MCP_EMAIL_HOST=0.0.0.0 \
    MCP_EMAIL_PORT=8101
EXPOSE 8101
CMD ["uv","run","--no-dev","python","-m","mcp_email"]
