"""Request/response models for the mailbox connection API."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Security = Literal["ssl", "starttls", "none"]


class _AccountFields(BaseModel):
    address: EmailStr
    username: str = Field(min_length=1, max_length=320)
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(ge=1, le=65535)
    imap_security: Security
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_security: Security


class EmailAccountIn(_AccountFields):
    """``password`` may be omitted to keep the stored one."""

    password: str | None = Field(default=None, min_length=1, max_length=512)


class EmailAccountOut(_AccountFields):
    has_password: bool


class ConnectionResult(BaseModel):
    ok: bool
    error: str | None = None
