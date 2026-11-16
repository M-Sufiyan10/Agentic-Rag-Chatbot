"""Tests for the MCP client: config loading, tool loading, guards and the health report.

Servers are launched over stdio with ``sys.executable``; nothing touches the network.
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import mcp as agent_mcp
from app.agent.graph import build_graph
from app.agent.mcp import load_mcp_config, load_mcp_tools
from app.chat.streaming import stream_agent_events
from app.config import Settings
from app.main import create_app
from tests.fakes import ScriptedChatModel, tool_call_reply

ECHO_SERVER = Path(__file__).resolve().parents[1] / "mcp_servers" / "echo_server.py"
ECHO: dict[str, Any] = {"transport": "stdio", "command": sys.executable, "args": [str(ECHO_SERVER)]}
COMMITTED_CONFIG = Path(__file__).resolve().parents[2] / "mcp_servers.json"


async def _load(
    connections: dict[str, dict[str, Any]],
    *,
    reserved_names: set[str] | None = None,
    tool_timeout: float = 10,
) -> tuple[dict[str, BaseTool], dict[str, str]]:
    tools, statuses = await load_mcp_tools(
        connections,
        reserved_names=reserved_names or set(),
        tool_timeout=tool_timeout,
        startup_timeout=20,
    )
    return {t.name: t for t in tools}, statuses


# --- config ---


def test_committed_config_has_utils_and_pinned_fetch() -> None:
    config = load_mcp_config(COMMITTED_CONFIG)
    assert list(config) == ["utils", "fetch"]
    args = config["fetch"]["args"]
    assert "mcp-server-fetch==2026.10.24" in args
    assert "--allow-private-ips" not in args


def test_committed_config_replaces_the_upstream_fetch_description() -> None:
    description = load_mcp_config(COMMITTED_CONFIG)["fetch"]["tool_descriptions"]["fetch"]
    assert "Read a web page" in description
    assert "did not have internet access" not in description


def test_relative_config_path_resolves_against_backend_dir() -> None:
    assert load_mcp_config(Path("mcp_servers.json")) == load_mcp_config(COMMITTED_CONFIG)


def test_missing_config_file_means_no_servers(tmp_path: Path) -> None:
    assert load_mcp_config(tmp_path / "nope.json") == {}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"x": {"transport": "carrier-pigeon"}}),
        json.dumps({"x": {"transport": "stdio"}}),
        json.dumps({"x": {"transport": "stdio", "command": "x", "args": [1]}}),
        json.dumps({"x": {"transport": "streamable_http"}}),
        json.dumps(["not", "an", "object"]),
        json.dumps({"x": {"transport": "stdio", "command": "x", "tool_descriptions": "x"}}),
        json.dumps({"x": {"transport": "stdio", "command": "x", "tool_descriptions": {"t": 1}}}),
    ],
)
def test_invalid_config_is_a_clear_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "servers.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="Invalid MCP config") as info:
        load_mcp_config(path)
    assert str(path) in str(info.value)


# --- loading ---


async def test_loads_echo_tools_and_calls_them() -> None:
    tools, statuses = await _load({"echo": ECHO})
    assert {"shout", "boom", "nap"} <= set(tools)
    assert statuses == {"echo": "ok"}
    assert await tools["shout"].ainvoke({"text": "hi"}) == "HI"


async def test_unavailable_server_is_skipped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = {"transport": "stdio", "command": "/nonexistent", "args": []}
    with caplog.at_level(logging.WARNING, logger=agent_mcp.__name__):
        tools, statuses = await _load({"broken": broken, "echo": ECHO})
    assert statuses == {"broken": "unavailable", "echo": "ok"}
    assert "shout" in tools
    assert any(
        r.levelno == logging.WARNING and "'broken' unavailable" in r.getMessage()
        for r in caplog.records
    )


async def test_tool_named_like_a_reserved_name_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=agent_mcp.__name__):
        tools, statuses = await _load({"echo": ECHO}, reserved_names={"shout"})
    assert "shout" not in tools
    assert {"boom", "nap"} <= set(tools)
    assert statuses == {"echo": "ok"}
    assert any(r.levelno == logging.WARNING and "shout" in r.getMessage() for r in caplog.records)


async def test_tool_named_like_an_earlier_mcp_tool_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=agent_mcp.__name__):
        tools_list, statuses = await load_mcp_tools(
            {"echo": ECHO, "echo2": ECHO},
            reserved_names=set(),
            tool_timeout=10,
            startup_timeout=20,
        )
    assert statuses == {"echo": "ok", "echo2": "ok"}
    assert sorted(t.name for t in tools_list) == ["boom", "nap", "shout"]
    assert any("echo2" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


# --- guard ---


async def test_slow_tool_call_times_out_with_an_error_string() -> None:
    tools, _ = await _load({"echo": ECHO}, tool_timeout=0.5)
    started = time.monotonic()
    result = await tools["nap"].ainvoke({"seconds": 5})
    assert time.monotonic() - started < 3
    assert result == "Error: the echo tool server didn't respond in 0.5s."


async def test_failing_tool_returns_its_error_text_without_raising() -> None:
    tools, _ = await _load({"echo": ECHO})
    result = await tools["boom"].ainvoke({})
    assert isinstance(result, str)
    assert "kaboom" in result


async def test_dead_server_returns_an_error_string() -> None:
    guarded = agent_mcp.guard_mcp_tool(_RaisingTool(name="shout"), server="echo", timeout=5)
    result = await guarded.ainvoke({"text": "hi"})
    assert result == "Error: the echo tool server failed (connection lost)."


class _RaisingTool(BaseTool):
    """A tool whose every call fails like a dead MCP server's would."""

    description: str = "fails"

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("connection lost")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("connection lost")


async def test_guarded_tool_keeps_name_description_and_schema() -> None:
    tools, _ = await _load({"echo": ECHO})
    shout = tools["shout"]
    assert shout.description == "Return the text in upper case."
    assert set(shout.args) == {"text"}


async def test_tool_descriptions_override_the_server_description() -> None:
    echo = {**ECHO, "tool_descriptions": {"shout": "Shout the text back."}}
    tools, statuses = await _load({"echo": echo})
    # The override isn't forwarded as a connection key, or the server would fail to start.
    assert statuses == {"echo": "ok"}
    assert tools["shout"].description == "Shout the text back."
    assert tools["boom"].description != "Shout the text back."
    assert await tools["shout"].ainvoke({"text": "hi"}) == "HI"
    assert echo["tool_descriptions"] == {"shout": "Shout the text back."}  # input not mutated


async def test_guard_uses_a_given_description() -> None:
    tools, _ = await _load({"echo": ECHO})
    guarded = agent_mcp.guard_mcp_tool(
        tools["shout"], server="echo", timeout=5, description="Custom."
    )
    assert guarded.description == "Custom."
    assert guarded.name == "shout"


# --- graph and app ---


async def test_agent_calls_an_mcp_tool() -> None:
    tools, _ = await _load({"echo": ECHO})
    model = ScriptedChatModel(
        replies=[tool_call_reply("shout", {"text": "hi"}, "c1"), AIMessage("It said HI")]
    )
    graph = build_graph(model, InMemorySaver(), list(tools.values()))
    events = [
        (event.event, json.loads(event.data))
        async for event in stream_agent_events(graph, "thread-1", "shout hi", "user-1")
    ]
    (tool_end,) = [data for name, data in events if name == "tool_end"]
    assert tool_end == {"id": "c1", "name": "shout", "output": "HI"}
    assert events[-1] == ("done", {"content": "It said HI"})


async def test_health_reports_mcp_server_status(
    tmp_path: Path,
    test_settings: Settings,
    checkpointer: InMemorySaver,
    embeddings: Embeddings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_web_search(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("building the app must not run a web search")

    monkeypatch.setattr("app.agent.tools.web_search.DDGS", no_web_search)
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"echo": ECHO}))
    application = create_app(
        test_settings.model_copy(update={"mcp_config_path": str(config)}),
        chat_model=ScriptedChatModel(replies=[]),
        checkpointer=checkpointer,
        embeddings=embeddings,
        tools=None,
    )
    async with LifespanManager(application, startup_timeout=30):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mcp": {"echo": "ok"}, "langfuse": "off"}
