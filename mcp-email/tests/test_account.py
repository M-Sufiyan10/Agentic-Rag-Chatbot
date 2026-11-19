import pytest

from mcp_email.account import Account, UnsafeAccountError, check_account_safety

PUBLIC = "93.184.215.14"


def make(**kw: object) -> Account:
    base: dict[str, object] = {
        "address": "a@example.com",
        "username": "a",
        "password": "pw",
        "imap_host": PUBLIC,
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_host": PUBLIC,
        "smtp_port": 465,
        "smtp_security": "ssl",
    }
    base.update(kw)
    return Account(**base)  # type: ignore[arg-type]


def test_none_security_refused_unless_allowed() -> None:
    acct = make(imap_security="none")
    with pytest.raises(UnsafeAccountError):
        check_account_safety(acct, allow_insecure=False)
    check_account_safety(acct, allow_insecure=True)


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254", "localhost"]
)
@pytest.mark.parametrize("field", ["imap_host", "smtp_host"])
def test_private_and_loopback_hosts_refused(host: str, field: str) -> None:
    acct = make(**{field: host})
    with pytest.raises(UnsafeAccountError):
        check_account_safety(acct, allow_insecure=False)


def test_public_ssl_host_allowed() -> None:
    check_account_safety(make(), allow_insecure=False)


def test_unresolvable_host_is_unsafe_error() -> None:
    with pytest.raises(UnsafeAccountError, match="can't resolve"):
        check_account_safety(make(imap_host="no-such-host.invalid"), allow_insecure=False)
