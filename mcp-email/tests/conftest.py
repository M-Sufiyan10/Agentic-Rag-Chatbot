import socket
import uuid
from collections.abc import Callable

import pytest

from mcp_email.account import Account


def greenmail_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3143), timeout=1):
            return True
    except OSError:
        return False


def make_account(address: str, password: str = "pw") -> Account:
    return Account(
        address=address,
        username=address,
        password=password,
        imap_host="127.0.0.1",
        imap_port=3143,
        imap_security="none",
        smtp_host="127.0.0.1",
        smtp_port=3025,
        smtp_security="none",
    )


@pytest.fixture
def greenmail_account() -> Callable[..., Account]:
    def factory(address: str | None = None, password: str = "pw") -> Account:
        return make_account(address or f"u{uuid.uuid4().hex}@example.com", password)

    return factory
