"""Password hashing (argon2) and JWT creation/validation."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.config import Settings

__all__ = [
    "InvalidTokenError",
    "TokenKind",
    "create_token",
    "decode_token",
    "hash_password",
    "verify_password",
]

TokenKind = Literal["access", "refresh"]

_ALGORITHM = "HS256"
_password_hash = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _password_hash.verify(plain, hashed)


def create_token(user_id: UUID, kind: TokenKind, settings: Settings) -> str:
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if kind == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    claims = {"sub": str(user_id), "type": kind, "exp": datetime.now(UTC) + ttl}
    return jwt.encode(claims, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str, expected: TokenKind, settings: Settings) -> UUID:
    """Return the user id, or raise InvalidTokenError (bad signature, expired, wrong type)."""
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[_ALGORITHM],
        options={"require": ["sub", "type", "exp"]},
    )
    if claims["type"] != expected:
        raise InvalidTokenError("wrong token type")
    try:
        return UUID(claims["sub"])
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("invalid subject") from exc
