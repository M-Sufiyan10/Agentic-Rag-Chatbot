"""FastAPI application factory."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.checkpointer import open_checkpointer
from app.agent.graph import build_graph
from app.agent.llm import build_chat_model
from app.agent.tools import build_tools
from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.config import Settings, get_settings
from app.conversations.router import router as conversations_router
from app.db import create_engine, create_sessionmaker
from app.documents.embeddings import build_embeddings
from app.documents.ingestion import fail_interrupted_documents
from app.documents.router import router as documents_router
from app.documents.storage import LocalFileStorage


def create_app(
    settings: Settings | None = None,
    *,
    chat_model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    tools: Sequence[BaseTool] | None = None,
    embeddings: Embeddings | None = None,
) -> FastAPI:
    """Build the app.

    ``chat_model``, ``checkpointer``, ``tools`` and ``embeddings`` are a test seam: when given, they
    are used instead of the configured LLM, the Postgres checkpointer and the
    built-in tools (``tools=[]`` disables tools).
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Create long-lived resources at startup and release them at shutdown.

        The agent graph is compiled once here and shared by all requests via
        ``app.state.graph``; per-conversation state lives in the checkpointer,
        keyed by thread id, so sharing the graph is safe.
        """
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.storage = LocalFileStorage(settings.upload_dir)
        app.state.embeddings = embeddings if embeddings is not None else build_embeddings(settings)
        await fail_interrupted_documents(app.state.sessionmaker)
        async with AsyncExitStack() as stack:
            stack.push_async_callback(engine.dispose)
            saver: BaseCheckpointSaver[Any]
            if checkpointer is not None:
                saver = checkpointer  # injected (tests): don't open the Postgres one
            else:
                saver = await stack.enter_async_context(open_checkpointer(settings.database_url))
            model = chat_model if chat_model is not None else build_chat_model(settings)
            app.state.graph = build_graph(
                model,
                saver,
                build_tools(
                    settings,
                    sessionmaker=app.state.sessionmaker,
                    embeddings=app.state.embeddings,
                )
                if tools is None
                else tools,
                max_tool_rounds=settings.agent_max_tool_rounds,
                max_history_tokens=settings.llm_max_history_tokens,
            )
            yield

    app = FastAPI(title="Chatbot API", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    api.include_router(auth_router)
    api.include_router(conversations_router)
    api.include_router(chat_router)
    api.include_router(documents_router)
    app.include_router(api)
    return app
