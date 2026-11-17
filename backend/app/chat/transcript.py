"""Build the conversation transcript from checkpointed graph messages."""

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.chat.schemas import CitationOut, MessageOut
from app.chat.streaming import citation_items, dedupe_citations


def final_answer_text(message: BaseMessage) -> str:
    """The text of a final answer, or ``""`` if ``message`` isn't one.

    A final answer is an ``AIMessage`` without tool calls that has text. ``.text``
    joins the text blocks of a content-block list (as hosted models such as Claude
    return, with thinking blocks alongside) and returns a plain string unchanged.
    """
    if not isinstance(message, AIMessage) or message.tool_calls:
        return ""
    return message.text


def completed_turn_ids(messages: Sequence[BaseMessage]) -> set[str]:
    """Ids of the user messages whose turn ended in a final, non-empty text answer."""
    ids: set[str] = set()
    turn_id: str | None = None
    for message in messages:
        if isinstance(message, HumanMessage):
            turn_id = message.id
        elif turn_id is not None and final_answer_text(message):
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
            text = final_answer_text(message)
            if text:
                vote = feedback.get(turn_id) if turn_id is not None else None
                transcript.append(
                    MessageOut(
                        id=turn_id,
                        role="assistant",
                        content=text,
                        tools=tools_used,
                        citations=[CitationOut(**c) for c in dedupe_citations(found)],
                        feedback=None if vote is None else ("up" if vote else "down"),
                    )
                )
                tools_used = []
                found = []
    return transcript
