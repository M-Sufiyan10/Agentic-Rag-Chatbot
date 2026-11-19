# mcp-email

An MCP server (Python `mcp` SDK, `MCPServer`) that reads and sends mail over IMAP/SMTP for an
account supplied on every call. It holds no credentials of its own.

```bash
uv sync
MCP_EMAIL_TOKEN=change-me uv run python -m mcp_email   # http://127.0.0.1:8101/mcp
uv run python -m mcp_email --stdio                     # stdio, no token needed
```

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCP_EMAIL_TOKEN` | (required for HTTP) | Bearer token; requests need `Authorization: Bearer <token>`. The process exits with code 2 if it is empty. |
| `MCP_EMAIL_HOST` | `127.0.0.1` | Bind address |
| `MCP_EMAIL_PORT` | `8101` | Bind port |
| `EMAIL_ALLOW_INSECURE_HOSTS` | unset | Dev only. When `true`, allows security `none` and loopback/private hosts (needed for the GreenMail test server). Never set it in production. |

## Tools

| Tool | Purpose |
| --- | --- |
| `check_connection` | Verify IMAP and SMTP login |
| `list_emails` | List recent inbox mail (limit 1-25, since_days 1-90); never marks mail read |
| `read_email` | Read one email by uid (body truncated at 8,000 chars) |
| `is_listed_sender` | Has this address ever emailed the mailbox? |
| `send_email` | Send plain text to ONE recipient who has emailed the mailbox before; optional `reply_to_uid` |

Email text is wrapped in `<email>` tags behind a note that it is untrusted data.

Checks: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .`
(GreenMail tests need `docker compose up -d greenmail` and skip otherwise.)
