"""The LangGraph agent: a model node and a tool node joined in a loop.

LangGraph concepts, in the order they appear below:

* **State** is the data that flows through the graph. Here it is
  `MessagesState`: a dict with a single `messages` list. Its reducer
  *appends* whatever a node returns under `messages`, rather than replacing
  the list, so conversation history accumulates.
* **Nodes** are plain (async) functions that take the current state and return
  a partial state update. `agent` sends the conversation to the model and
  returns the reply as `{"messages": [reply]}`. `tools` is LangGraph's prebuilt
  `ToolNode`: it runs every tool call on the model's last message and appends
  one `ToolMessage` per call. Tool errors (unknown tool, bad arguments) become
  error `ToolMessage`s so the model can see them and recover.
* **Edges** define control flow. `_route_after_agent` routes `agent -> tools` when
  the reply contains tool calls and `agent -> END` otherwise; `tools -> agent`
  closes the loop. Without any tools the graph is just `START -> agent -> END`.
* **Checkpointer** saves the state after each step, keyed by the `thread_id`
  in the run config. Invoking the graph again with the same `thread_id` loads
  the saved history first, so the caller only sends the *new* message.

Two guards live inside the `agent` node:

* **Max tool rounds.** A "round" is one model reply that requests tools. Rounds
  are counted per turn (after the latest human message). Once the limit is hit
  the model is called *without* tools and with an extra instruction to answer
  now, so a looping model always ends with text.
* **History trimming.** Only the model's view is trimmed to a token budget
  (starting on a human message, so a tool call is never separated from its
  result); the full history stays in state.

The system prompt is prepended inside the node on every call and is never
written to state, so it is not duplicated in the checkpoint and can be changed
without touching stored conversations.

**Documents note.** A small model's prior ("I can't see your files") beats a
static instruction to use `search_documents`, so each turn the server tells it
which documents *this* user has. The chat endpoint looks them up for the
authenticated user and passes them as one nested dict,
`config["configurable"]["documents"]`; the node appends
`prompts.documents_note(...)` to the same system message. Being a dict matters:
LangGraph copies top-level `str`/`int`/`float`/`bool` configurable values into
every checkpoint's metadata, but not dicts, so filenames never reach the
checkpoint. Users without ready or processing documents get the plain prompt.

**Prefetch.** With ``prefetch`` given, a ``prefetch`` node runs first, once per
turn (``START -> prefetch -> agent``), and adds server-side retrieval results
as an ordinary tool call plus result before the model is called. Small models
don't reliably call ``web_search`` or ``search_documents`` themselves; see
``app.agent.prefetch``.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.agent.prefetch import Prefetcher
from app.agent.prompts import FINAL_ROUND_INSTRUCTION, base_system_prompt, documents_note


def _tool_rounds_this_turn(messages: Sequence[BaseMessage]) -> int:
    """Count AI messages that requested tools since the latest human message."""
    rounds = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and message.tool_calls:
            rounds += 1
    return rounds


def _route_after_agent(state: MessagesState) -> str:
    """Go to `tools` if the last message requests tools, else end the run.

    Like the prebuilt `tools_condition`, except that an empty history routes to
    END instead of raising: rolling back a thread's first turn (see
    `app.chat.streaming`) removes every message via `update_state(as_node="agent")`,
    which re-evaluates this edge on the emptied state.
    """
    messages = state["messages"]
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tools"
    return END


def _trim_history(messages: Sequence[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    trimmed = trim_messages(
        list(messages),
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=max_tokens,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
    if trimmed:
        return trimmed
    # A single message over budget: keep the latest human message rather than nothing.
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    return [last_human] if last_human is not None else list(messages)


def build_graph(
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver[Any],
    tools: Sequence[BaseTool] = (),
    *,
    max_tool_rounds: int = 5,
    max_history_tokens: int = 6000,
    prefetch: Prefetcher | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the agent graph around `model`, persisting state via `checkpointer`."""
    # Bound once at build time; with no tools the plain model is used (bind_tools never called).
    tool_model: Any = model.bind_tools(list(tools)) if tools else model

    async def agent(state: MessagesState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        history = _trim_history(state["messages"], max_history_tokens)
        system = base_system_prompt(datetime.now(UTC).date())
        note = documents_note((config.get("configurable") or {}).get("documents"))
        if note:
            system += "\n\n" + note
        prompt: list[BaseMessage] = [SystemMessage(system), *history]
        if tools and _tool_rounds_this_turn(state["messages"]) >= max_tool_rounds:
            response = await model.ainvoke([*prompt, SystemMessage(FINAL_ROUND_INSTRUCTION)])
        else:
            response = await tool_model.ainvoke(prompt)
        return {"messages": [response]}

    async def prefetch_node(
        state: MessagesState, config: RunnableConfig
    ) -> dict[str, list[BaseMessage]]:
        assert prefetch is not None
        last_human = next(
            (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
        )
        if last_human is None or not last_human.text:
            return {"messages": []}
        return {"messages": await prefetch(last_human.text, config)}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    if prefetch is not None:
        builder.add_node("prefetch", prefetch_node)
        builder.add_edge(START, "prefetch")
        builder.add_edge("prefetch", "agent")
    else:
        builder.add_edge(START, "agent")
    if tools:
        builder.add_node("tools", ToolNode(tools))
        builder.add_conditional_edges("agent", _route_after_agent, ["tools", END])
        builder.add_edge("tools", "agent")
    else:
        builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)
