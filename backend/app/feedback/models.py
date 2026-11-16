"""Per-turn thumbs feedback ORM model."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MessageFeedback(Base):
    """A user's thumbs up/down on one assistant reply, identified by the turn id.

    ``turn_id`` is the id of the turn's ``HumanMessage`` in the graph state, so it
    has no foreign key. ``created_at`` is the first vote's time and never changes.
    """

    __tablename__ = "message_feedback"
    __table_args__ = (UniqueConstraint("conversation_id", "turn_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    turn_id: Mapped[uuid.UUID] = mapped_column()
    positive: Mapped[bool] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
