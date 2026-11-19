import os
import socket

import pytest
from mcp.types import CallToolResult, TextContent

from app.email.client import EmailServerClient, EmailServerError, _result_value

URL = "http://127.0.0.1:8101/mcp"
TOKEN_VAR = "MCP_EMAIL_TEST_TOKEN"


def _server_available() -> bool:
    if not os.getenv(TOKEN_VAR):
        return False
    try:
        with socket.create_connection(("127.0.0.1", 8101), timeout=0.5):
            return True
    except OSError:
        return False


needs_server = pytest.mark.skipif(
    not _server_available(), reason="needs mcp-email on :8101 and MCP_EMAIL_TEST_TOKEN"
)

GREENMAIL = {
    "address": "u@localhost",
    "username": "u@localhost",
    "password": "u",
    "imap_host": "greenmail",
    "imap_port": 3143,
    "imap_security": "none",
    "smtp_host": "greenmail",
    "smtp_port": 3025,
    "smtp_security": "none",
}


@needs_server
async def test_call_against_real_server() -> None:
    client = EmailServerClient(URL, os.environ[TOKEN_VAR], timeout=20)
    result = await client.call("check_connection", {"account": GREENMAIL})
    assert isinstance(result, dict)
    assert "ok" in result
    assert await client.ping() is True


@needs_server
async def test_wrong_token_raises() -> None:
    client = EmailServerClient(URL, "wrong-token", timeout=10)
    with pytest.raises(EmailServerError):
        await client.call("check_connection", {"account": GREENMAIL})


async def test_timeout_raises_email_server_error() -> None:
    client = EmailServerClient("http://10.255.255.1:9/mcp", "tok", timeout=0.5)
    with pytest.raises(EmailServerError) as info:
        await client.call("check_connection", {"account": {"password": "hunter2-secret"}})
    assert "hunter2-secret" not in str(info.value)


def _result(structured: dict[str, object] | None, text: str = "") -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structuredContent=structured
    )


def test_result_value_unwraps_primitive_result() -> None:
    assert _result_value(_result({"result": "1. From a"}, "1. From a")) == "1. From a"


def test_result_value_keeps_dicts_and_text() -> None:
    assert _result_value(_result({"listed": True, "count": 1})) == {"listed": True, "count": 1}
    assert _result_value(_result({"result": "x", "other": 1})) == {"result": "x", "other": 1}
    assert _result_value(_result(None, "plain")) == "plain"


@needs_server
async def test_list_emails_returns_plain_text_against_real_server() -> None:
    client = EmailServerClient(URL, os.getenv(TOKEN_VAR, ""), timeout=20)
    result = await client.call("list_emails", {"account": GREENMAIL})
    assert isinstance(result, str)
    assert not result.startswith("{'result'")
