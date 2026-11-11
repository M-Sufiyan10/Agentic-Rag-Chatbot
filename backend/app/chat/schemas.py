"""Request/response models for the chat (messages) endpoints."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints


class MessageIn(BaseModel):
    """A message the user sends. Surrounding whitespace is stripped first."""

    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class MessageOut(BaseModel):
    """One entry of a conversation transcript."""

    role: Literal["user", "assistant"]
    content: str
    tools: list[str] = []
    """For an assistant message, the tools called since the previous user message, in order."""
