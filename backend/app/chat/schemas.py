"""Request/response models for the chat (messages) endpoints."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


class MessageIn(BaseModel):
    """A message the user sends. Surrounding whitespace is stripped first."""

    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class CitationOut(BaseModel):
    """A document chunk the assistant's answer drew on."""

    document_id: str
    filename: str
    chunk_index: int
    snippet: str


class MessageOut(BaseModel):
    """One entry of a conversation transcript."""

    id: str | None = None
    """For an assistant message, the turn id (the user message's id); ``None`` for user messages."""
    role: Literal["user", "assistant"]
    content: str
    tools: list[str] = []
    """For an assistant message, the tools called since the previous user message, in order."""
    citations: list[CitationOut] = Field(default_factory=list)
    """For an assistant message, the de-duplicated document chunks cited this turn."""
    feedback: Literal["up", "down"] | None = None
    """For an assistant message, the caller's thumbs vote on it, if any."""
