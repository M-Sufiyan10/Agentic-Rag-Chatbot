"""Integration test: the Postgres checkpointer against the test database."""

import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from app.agent.checkpointer import open_checkpointer
from tests.conftest import TEST_DATABASE_URL


async def test_open_checkpointer_against_test_db() -> None:
    thread_id = f"test-{uuid.uuid4()}"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    async with open_checkpointer(TEST_DATABASE_URL) as saver:  # runs setup()
        checkpoint = empty_checkpoint()
        saved = await saver.aput(config, checkpoint, {"source": "input", "step": -1}, {})
        try:
            loaded = await saver.aget_tuple(config)
            assert loaded is not None
            assert loaded.checkpoint["id"] == checkpoint["id"]
            assert saved["configurable"]["thread_id"] == thread_id
        finally:
            await saver.adelete_thread(thread_id)
        assert await saver.aget_tuple(config) is None
