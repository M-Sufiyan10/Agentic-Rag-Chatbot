"""Email ORM models: a user's mailbox account and agent-drafted outgoing emails."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EmailAccount(Base):
    """A user's mailbox connection; the password is stored Fernet-encrypted."""

    __tablename__ = "email_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    address: Mapped[str] = mapped_column()
    username: Mapped[str] = mapped_column()
    password_encrypted: Mapped[str] = mapped_column(Text)
    imap_host: Mapped[str] = mapped_column()
    imap_port: Mapped[int] = mapped_column()
    imap_security: Mapped[str] = mapped_column()
    smtp_host: Mapped[str] = mapped_column()
    smtp_port: Mapped[int] = mapped_column()
    smtp_security: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class EmailDraft(Base):
    """An email the agent drafted; it is only sent after the user confirms.

    ``turn_id`` is the id of the turn's ``HumanMessage`` in the graph state, so it
    has no foreign key. ``status`` is pending|sending|sent|cancelled|failed.
    """

    __tablename__ = "email_drafts"
    __table_args__ = (Index("ix_email_drafts_conversation_id", "conversation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column()
    to_address: Mapped[str] = mapped_column()
    subject: Mapped[str] = mapped_column()
    body: Mapped[str] = mapped_column(Text)
    reply_to_uid: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
