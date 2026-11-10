"""The Postgres-backed LangGraph checkpointer and its lifecycle.

A *checkpointer* is how a LangGraph graph remembers things between calls.
After every step it saves the graph state (for us: the message list) as a
"checkpoint" keyed by the ``thread_id`` in the run config. We use one thread
per conversation (``thread_id = str(conversation.id)``), so the checkpointer
*is* our chat history store; there is no separate messages table.

``AsyncPostgresSaver`` stores checkpoints in its own tables (``checkpoints``,
``checkpoint_blobs``, ``checkpoint_writes``, ``checkpoint_migrations``), which
``setup()`` creates and migrates idempotently. These tables are owned by
LangGraph, not by our Alembic migrations.

It talks to Postgres through psycopg 3 (not asyncpg like SQLAlchemy does), so
it needs its own connection pool and a plain ``postgresql://`` URL.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool


def to_psycopg_url(database_url: str) -> str:
    """Turn our SQLAlchemy URL (``postgresql+asyncpg://...``) into a psycopg one."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def open_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Open a connection pool, prepare the checkpoint tables and yield the saver.

    Meant to be entered once for the app's lifetime (in the FastAPI lifespan);
    the pool is closed when the context exits.

    The connection options are the ones ``AsyncPostgresSaver`` requires:

    * ``autocommit=True``: each checkpoint write is committed immediately
      (the saver manages its own transactions where it needs them).
    * ``prepare_threshold=0``: disable server-side prepared statements, which
      break behind connection poolers such as PgBouncer.
    * ``row_factory=dict_row``: the saver reads rows as dicts.
    """
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        to_psycopg_url(database_url),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    async with pool:  # opens the pool now, closes it on exit
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
