"""Request/response models for the mailbox connection API."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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


class EmailDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    turn_id: uuid.UUID
    to_address: str
    subject: str
    body: str
    reply_to_uid: str | None
    status: str
    error: str | None
    created_at: datetime
    sent_at: datetime | None
