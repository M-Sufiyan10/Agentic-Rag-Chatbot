"""Web search via DuckDuckGo, exposed to the agent as the `web_search` tool."""

import asyncio

from ddgs import DDGS
from langchain_core.tools import BaseTool, tool


def make_web_search_tool(max_results: int, timeout: int) -> BaseTool:
    @tool
    async def web_search(query: str) -> str:
        """Search the web. Use this for recent events, facts that change over time, or anything
        that may have happened after your training data. Returns titles, URLs and snippets.
        """
        try:
            results = await asyncio.to_thread(
                lambda: DDGS(timeout=timeout).text(query, max_results=max_results)
            )
        except Exception as exc:
            return (
                f"Error: web search failed ({exc}). "
                "Answer from what you know and say the search failed."
            )
        if not results:
            return "No results found."
        return "\n\n".join(
            f"{n}. {r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
            for n, r in enumerate(results, start=1)
        )

    return web_search
