import uuid

import pytest

from app.auth.security import (
    InvalidTokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.config import Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(jwt_secret="test-secret-" + "x" * 32, **overrides)  # type: ignore[arg-type]


def test_hash_roundtrip() -> None:
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed)
    assert not verify_password("wrong", hashed)


def test_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_token_roundtrip_returns_user_id() -> None:
    settings = make_settings()
    user_id = uuid.uuid4()
    token = create_token(user_id, "access", settings)
    assert decode_token(token, "access", settings) == user_id


def test_refresh_token_rejected_as_access() -> None:
    settings = make_settings()
    token = create_token(uuid.uuid4(), "refresh", settings)
    with pytest.raises(InvalidTokenError):
        decode_token(token, "access", settings)


def test_expired_token_rejected() -> None:
    settings = make_settings(access_token_ttl_minutes=-1)
    token = create_token(uuid.uuid4(), "access", settings)
    with pytest.raises(InvalidTokenError):
        decode_token(token, "access", settings)


def test_token_signed_with_other_secret_rejected() -> None:
    token = create_token(uuid.uuid4(), "access", make_settings())
    other = Settings(jwt_secret="other-secret-" + "y" * 32)
    with pytest.raises(InvalidTokenError):
        decode_token(token, "access", other)
