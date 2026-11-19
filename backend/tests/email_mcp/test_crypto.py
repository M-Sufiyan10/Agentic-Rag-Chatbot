import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.config import Settings
from app.email.crypto import decrypt_password, encrypt_password


def _key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


def test_round_trip() -> None:
    key = _key()
    assert decrypt_password(encrypt_password("s3cret", key), key) == "s3cret"


def test_ciphertext_differs_from_plain_and_between_calls() -> None:
    key = _key()
    a, b = encrypt_password("s3cret", key), encrypt_password("s3cret", key)
    assert "s3cret" not in a
    assert a != b


def test_wrong_key_raises() -> None:
    token = encrypt_password("s3cret", _key())
    with pytest.raises(InvalidToken):
        decrypt_password(token, _key())


def test_invalid_key_rejected_by_settings() -> None:
    with pytest.raises(ValueError, match="EMAIL_ENCRYPTION_KEY"):
        Settings(email_encryption_key="nope")  # type: ignore[arg-type]


def test_email_enabled_needs_both() -> None:
    key = Fernet.generate_key().decode()
    assert not Settings(email_encryption_key=None, mcp_email_token=None).email_enabled
    assert not Settings(email_encryption_key=key, mcp_email_token=None).email_enabled
    assert not Settings(email_encryption_key=None, mcp_email_token="t").email_enabled
    assert not Settings(email_encryption_key=key, mcp_email_token="").email_enabled
    assert Settings(email_encryption_key=key, mcp_email_token="t").email_enabled
