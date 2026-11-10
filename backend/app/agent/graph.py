"""The LangGraph agent: a one-node graph that calls the chat model.

LangGraph concepts, in the order they appear below:

* **State** is the data that flows through the graph. Here it is
  `MessagesState`: a dict with a single `messages` list. Its reducer
  *appends* whatever a node returns under `messages`, rather than replacing
  the list, so conversation history accumulates.
* **Nodes** are plain (async) functions that take the current state and return
  a partial state update. Our only node, `agent`, sends the conversation to the
  model and returns the reply as `{"messages": [reply]}`.
* **Edges** define control flow. `START -> agent -> END` means: run `agent`
  once, then stop. Later milestones add tools by inserting more nodes and
  conditional edges (agent -> tools -> agent loop).
* **Checkpointer** saves the state after each step, keyed by the `thread_id`
  in the run config. Invoking the graph again with the same `thread_id` loads
  the saved history first, so the caller only sends the *new* message.

The system prompt is prepended inside the node on every call and is never
written to state, so it is not duplicated in the checkpoint and can be changed
without touching stored conversations.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.prompts import SYSTEM_PROMPT


def build_graph(
    model: BaseChatModel, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the agent graph around `model`, persisting state via `checkpointer`."""

    async def agent(state: MessagesState) -> dict[str, list[BaseMessage]]:
        response = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)
