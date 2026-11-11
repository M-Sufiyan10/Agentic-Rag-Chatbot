from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.agent.prompts import FINAL_ROUND_INSTRUCTION, SYSTEM_PROMPT
from tests.fakes import ScriptedChatModel, echo, tool_call_reply


async def test_graph_replies_and_persists_history() -> None:
    model = GenericFakeChatModel(messages=iter([AIMessage("Hi!"), AIMessage("Again!")]))
    graph = build_graph(model, InMemorySaver())
    cfg: RunnableConfig = {"configurable": {"thread_id": "t1"}}
    await graph.ainvoke({"messages": [HumanMessage("hello")]}, cfg)
    out = await graph.ainvoke({"messages": [HumanMessage("and again")]}, cfg)
    assert [m.content for m in out["messages"]] == ["hello", "Hi!", "and again", "Again!"]


def cfg(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


def has_final_instruction(messages: list[BaseMessage]) -> bool:
    return any(
        isinstance(m, SystemMessage) and m.content == FINAL_ROUND_INSTRUCTION for m in messages
    )


async def test_tool_loop_runs_tool_then_answers() -> None:
    model = ScriptedChatModel(
        replies=[tool_call_reply("echo", {"text": "hi"}, "c1"), AIMessage("done: hi")]
    )
    graph = build_graph(model, InMemorySaver(), [echo])
    result = await graph.ainvoke({"messages": [HumanMessage("x")]}, cfg("t1"))
    kinds = [type(m).__name__ for m in result["messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert result["messages"][2].content == "hi"
    assert result["messages"][-1].content == "done: hi"


async def test_no_tools_never_binds() -> None:
    model = ScriptedChatModel(replies=[AIMessage("hi")])
    graph = build_graph(model, InMemorySaver())
    await graph.ainvoke({"messages": [HumanMessage("x")]}, cfg("t1"))
    assert model.call_tools == [None]


async def test_max_tool_rounds_forces_answer_without_tools() -> None:
    model = ScriptedChatModel(
        replies=[
            tool_call_reply("echo", {"text": "a"}, "c1"),
            tool_call_reply("echo", {"text": "b"}, "c2"),
            AIMessage("final"),
        ]
    )
    graph = build_graph(model, InMemorySaver(), [echo], max_tool_rounds=2)
    result = await graph.ainvoke({"messages": [HumanMessage("x")]}, cfg("t1"))
    assert sum(isinstance(m, ToolMessage) for m in result["messages"]) == 2
    assert len(model.calls) == 3
    assert has_final_instruction(model.calls[-1])
    assert model.call_tools == [["echo"], ["echo"], None]
    assert result["messages"][-1].content == "final"


async def test_rounds_counted_per_turn_not_per_conversation() -> None:
    model = ScriptedChatModel(
        replies=[
            tool_call_reply("echo", {"text": "a"}, "c1"),
            tool_call_reply("echo", {"text": "b"}, "c2"),
            AIMessage("first answer"),
            tool_call_reply("echo", {"text": "c"}, "c3"),
            AIMessage("second answer"),
        ]
    )
    graph = build_graph(model, InMemorySaver(), [echo], max_tool_rounds=2)
    await graph.ainvoke({"messages": [HumanMessage("one")]}, cfg("t1"))
    result = await graph.ainvoke({"messages": [HumanMessage("two")]}, cfg("t1"))
    assert not has_final_instruction(model.calls[3])
    assert model.call_tools[3] == ["echo"]
    assert result["messages"][-1].content == "second answer"


async def test_history_is_trimmed_for_the_model_but_kept_in_state() -> None:
    turns = 20
    model = ScriptedChatModel(replies=[AIMessage("answer " * 20) for _ in range(turns)])
    graph = build_graph(model, InMemorySaver(), max_history_tokens=50)
    result: dict[str, list[BaseMessage]] = {}
    for i in range(turns):
        result = await graph.ainvoke({"messages": [HumanMessage(f"question {i} " * 20)]}, cfg("t1"))
    received = model.calls[-1]
    assert len(received) < len(result["messages"]) / 4
    assert len(result["messages"]) == turns * 2
    non_system = [m for m in received if not isinstance(m, SystemMessage)]
    assert isinstance(non_system[0], HumanMessage)


async def test_trimming_never_splits_tool_call_from_result() -> None:
    model = ScriptedChatModel(replies=[AIMessage("ok")])
    graph = build_graph(model, InMemorySaver(), [echo], max_history_tokens=5)
    history = [
        HumanMessage("please echo this long message " * 5),
        tool_call_reply("echo", {"text": "hello " * 20}, "c1"),
        ToolMessage("hello " * 20, tool_call_id="c1"),
    ]
    await graph.ainvoke({"messages": history}, cfg("t1"))
    received = model.calls[0]
    for i, m in enumerate(received):
        if isinstance(m, ToolMessage):
            prev = received[i - 1]
            assert isinstance(prev, AIMessage)
            assert m.tool_call_id in [c["id"] for c in prev.tool_calls]


def test_system_prompt_mentions_tools() -> None:
    assert "web_search" in SYSTEM_PROMPT
    assert "current_datetime" in SYSTEM_PROMPT
    assert "calculator" in SYSTEM_PROMPT
