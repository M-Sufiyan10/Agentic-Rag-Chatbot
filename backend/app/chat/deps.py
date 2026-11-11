"""Dependencies shared by the chat and conversation routers."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from langgraph.graph.state import CompiledStateGraph

# Conversations whose reply is currently streaming. One reply at a time per
# conversation: two concurrent runs on the same LangGraph thread would race on
# its checkpoints. Process-local, which is enough for a single-process server.
in_progress_conversations: set[UUID] = set()

Graph = CompiledStateGraph[Any, Any, Any, Any]


def get_graph(request: Request) -> Graph:
    """Return the compiled agent graph built in the app lifespan (``app.state.graph``)."""
    graph: Graph = request.app.state.graph
    return graph


GraphDep = Annotated[Graph, Depends(get_graph)]
