from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph


async def test_graph_replies_and_persists_history() -> None:
    model = GenericFakeChatModel(messages=iter([AIMessage("Hi!"), AIMessage("Again!")]))
    graph = build_graph(model, InMemorySaver())
    cfg: RunnableConfig = {"configurable": {"thread_id": "t1"}}
    await graph.ainvoke({"messages": [HumanMessage("hello")]}, cfg)
    out = await graph.ainvoke({"messages": [HumanMessage("and again")]}, cfg)
    assert [m.content for m in out["messages"]] == ["hello", "Hi!", "and again", "Again!"]
