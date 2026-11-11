import time
from datetime import datetime
from typing import Any

import pytest

from app.agent.tools import build_tools, datetime_tool, web_search
from app.agent.tools.calculator import calculator, evaluate
from app.agent.tools.datetime_tool import current_datetime
from app.config import Settings


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("2 ** 10", 1024),
        ("-7 // 2", -4),
        ("10 % 3", 1),
        ("sqrt(16)", 4.0),
        ("round(pi, 2)", 3.14),
        ("abs(-3)", 3),
    ],
)
def test_evaluate_arithmetic(expr: str, expected: float) -> None:
    assert evaluate(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "open('x')",
        "a + 1",
        "2 ** 100000",
        "9 ** 9 ** 9",
        "1 / 0",
        "[1, 2]",
        "'a' * 3",
        "",
        "x.y",
        "(((10**6)**100)**99)**99",
        "(1000000**100)**99",
        "1e308*10",
    ],
)
def test_evaluate_rejects(expr: str) -> None:
    with pytest.raises(ValueError):
        evaluate(expr)


async def test_calculator_tool_returns_error_string_instead_of_raising() -> None:
    assert (await calculator.ainvoke({"expression": "1/0"})).startswith("Error: ")
    assert await calculator.ainvoke({"expression": "6*7"}) == "42"


def _patch_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datetime_tool, "_now", lambda tz: datetime(2026, 11, 11, 9, 30, tzinfo=tz))


async def test_current_datetime_utc_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_now(monkeypatch)
    out = await current_datetime.ainvoke({})
    assert "2026-11-11T09:30:00+00:00" in out and "Wednesday" in out and "UTC" in out


async def test_current_datetime_named_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_now(monkeypatch)
    out = await current_datetime.ainvoke({"timezone": "Asia/Karachi"})
    assert "Asia/Karachi" in out and "+05:00" in out


async def test_current_datetime_unknown_timezone() -> None:
    assert (await current_datetime.ainvoke({"timezone": "Mars/Base"})).startswith("Error: ")


def _fake_ddgs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[dict[str, str]],
    error: Exception | None = None,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class FakeDDGS:
        def __init__(self, **kwargs: Any) -> None:
            seen["init"] = kwargs

        def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
            seen["query"] = query
            seen["text"] = kwargs
            if error:
                raise error
            return results

    monkeypatch.setattr(web_search, "DDGS", FakeDDGS)
    return seen


async def test_web_search_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_ddgs(
        monkeypatch,
        results=[
            {"title": "LangGraph 1.2", "href": "https://example.com/a", "body": "Released..."}
        ],
    )
    tool = web_search.make_web_search_tool(max_results=3, timeout=7)
    out = await tool.ainvoke({"query": "langgraph"})
    assert "1. LangGraph 1.2" in out
    assert "https://example.com/a" in out
    assert "Released..." in out
    assert seen["text"]["max_results"] == 3
    assert seen["init"]["timeout"] == 7
    assert seen["text"]["backend"] == Settings().web_search_backend
    assert seen["query"] == "langgraph"


async def test_web_search_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ddgs(monkeypatch, results=[])
    tool = web_search.make_web_search_tool(max_results=5, timeout=10)
    assert await tool.ainvoke({"query": "zzz"}) == "No results found."


async def test_web_search_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ddgs(monkeypatch, results=[], error=RuntimeError("ratelimit"))
    tool = web_search.make_web_search_tool(max_results=5, timeout=10)
    out = await tool.ainvoke({"query": "x"})
    assert out.startswith("Error: web search failed")
    assert "ratelimit" in out


async def test_web_search_backend_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_ddgs(monkeypatch, results=[])
    tool = web_search.make_web_search_tool(max_results=5, timeout=10, backend="brave,yahoo")
    await tool.ainvoke({"query": "x"})
    assert seen["text"]["backend"] == "brave,yahoo"


async def test_web_search_deadline_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowDDGS:
        def __init__(self, **kwargs: Any) -> None: ...

        def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
            time.sleep(1)
            return []

    monkeypatch.setattr(web_search, "DDGS", SlowDDGS)
    monkeypatch.setattr(web_search, "_DEADLINE_MARGIN_SECONDS", 0.0)
    tool = web_search.make_web_search_tool(max_results=5, timeout=0.05)
    out = await tool.ainvoke({"query": "x"})
    assert out.startswith("Error: web search timed out")


def test_build_tools_names(test_settings: Settings) -> None:
    names = [t.name for t in build_tools(test_settings)]
    assert names == ["calculator", "current_datetime", "web_search"]


@pytest.mark.parametrize("expr", ["-" * 5000 + "1", "1+" * 5000 + "1"])
async def test_deeply_nested_expression_is_an_error_not_a_crash(expr: str) -> None:
    with pytest.raises(ValueError):
        evaluate(expr)
    assert (await calculator.ainvoke({"expression": expr})).startswith("Error: ")


@pytest.mark.parametrize("expr", ["(((10**6)**100)**99)**99", "1e308*10"])
async def test_oversized_results_are_rejected_fast(expr: str) -> None:
    start = time.perf_counter()
    assert (await calculator.ainvoke({"expression": expr})).startswith("Error: ")
    assert time.perf_counter() - start < 0.5


def test_web_search_backend_validation() -> None:
    Settings()
    Settings(web_search_backend="auto")
    with pytest.raises(ValueError, match="duckduckgo"):
        Settings(web_search_backend="bing")
