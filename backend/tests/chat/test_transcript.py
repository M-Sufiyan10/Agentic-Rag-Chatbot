"""Transcript building from checkpointed messages, for string and content-block replies."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.chat.transcript import build_transcript, completed_turn_ids

# Hosted models such as Claude return a list of content blocks, not a string.
BLOCK_REPLY = [
    {"type": "thinking", "thinking": "", "signature": "sig"},
    {"type": "text", "text": "Lahore is sunny."},
]


def test_block_list_answer_is_a_completed_turn_and_shown() -> None:
    messages = [HumanMessage("weather?", id="t1"), AIMessage(BLOCK_REPLY)]

    assert completed_turn_ids(messages) == {"t1"}
    [user, answer] = build_transcript(messages, {})
    assert user.content == "weather?"
    assert answer.role == "assistant"
    assert answer.content == "Lahore is sunny."
    assert answer.id == "t1"


def test_block_list_tool_call_turn_keeps_chips_on_final_answer() -> None:
    call = AIMessage(
        [{"type": "thinking", "thinking": "", "signature": "s"}],
        tool_calls=[{"name": "get_weather", "args": {"location": "Lahore"}, "id": "c1"}],
    )
    messages = [
        HumanMessage("weather?", id="t1"),
        call,
        ToolMessage("sunny", tool_call_id="c1", name="get_weather"),
        AIMessage(BLOCK_REPLY),
    ]

    [_, answer] = build_transcript(messages, {"t1": True})
    assert answer.tools == ["get_weather"]
    assert answer.feedback == "up"


def test_reply_with_only_thinking_blocks_is_not_an_answer() -> None:
    messages = [
        HumanMessage("hi", id="t1"),
        AIMessage([{"type": "thinking", "thinking": "", "signature": "s"}]),
    ]

    assert completed_turn_ids(messages) == set()
    assert [m.role for m in build_transcript(messages, {})] == ["user"]


def test_plain_string_answers_still_work() -> None:
    messages = [HumanMessage("hi", id="t1"), AIMessage("hello")]

    assert completed_turn_ids(messages) == {"t1"}
    assert build_transcript(messages, {})[1].content == "hello"
