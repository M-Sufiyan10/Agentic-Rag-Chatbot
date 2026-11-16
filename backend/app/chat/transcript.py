"""Build the conversation transcript from checkpointed graph messages."""

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.chat.schemas import CitationOut, MessageOut
from app.chat.streaming import citation_items, dedupe_citations


def completed_turn_ids(messages: Sequence[BaseMessage]) -> set[str]:
    """Ids of the user messages whose turn ended in a final, non-empty text answer."""
    ids: set[str] = set()
    turn_id: str | None = None
    for message in messages:
        if isinstance(message, HumanMessage):
            turn_id = message.id
        elif (
            isinstance(message, AIMessage)
            and turn_id is not None
            and not message.tool_calls
            and isinstance(message.content, str)
            and message.content
        ):
            ids.add(turn_id)
    return ids


def build_transcript(
    messages: Sequence[BaseMessage], feedback: Mapping[str, bool]
) -> list[MessageOut]:
    """User and assistant messages with text; ``feedback`` maps turn id to thumbs-up."""
    transcript: list[MessageOut] = []
    tools_used: list[str] = []
    found: list[dict[str, Any]] = []
    turn_id: str | None = None
    for message in messages:
        if isinstance(message, HumanMessage):
            tools_used = []
            found = []
            turn_id = message.id
            if isinstance(message.content, str) and message.content:
                transcript.append(MessageOut(role="user", content=message.content))
        elif isinstance(message, ToolMessage):
            found.extend(citation_items(message))
        elif isinstance(message, AIMessage):
            tools_used.extend(call["name"] for call in message.tool_calls)
            # A preamble that came with tool calls is not an answer; the chips attach to
            # the final reply, matching the live view.
            if message.tool_calls:
                continue
            if isinstance(message.content, str) and message.content:
                vote = feedback.get(turn_id) if turn_id is not None else None
                transcript.append(
                    MessageOut(
                        id=turn_id,
                        role="assistant",
                        content=message.content,
                        tools=tools_used,
                        citations=[CitationOut(**c) for c in dedupe_citations(found)],
                        feedback=None if vote is None else ("up" if vote else "down"),
                    )
                )
                tools_used = []
                found = []
    return transcript
