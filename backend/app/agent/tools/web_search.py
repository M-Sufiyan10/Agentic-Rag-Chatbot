"""Web search via the keyless ddgs library, exposed to the agent as the `web_search` tool."""

import asyncio

from ddgs import DDGS
from langchain_core.tools import BaseTool, tool

# Margin so ddgs's own per-engine timeout fires before this outer deadline.
_DEADLINE_MARGIN_SECONDS = 5.0


def make_web_search_tool(
    max_results: int, timeout: float, backend: str = "duckduckgo,brave,yahoo,startpage"
) -> BaseTool:
    @tool
    async def web_search(query: str) -> str:
        """Search the web. Use this for recent events, facts that change over time, or anything
        that may have happened after your training data. Returns titles, URLs and snippets.
        """
        try:
            # The worker thread cannot be cancelled, but the turn stops waiting for it.
            results = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: DDGS(timeout=int(timeout)).text(
                        query, max_results=max_results, backend=backend
                    )
                ),
                timeout=timeout + _DEADLINE_MARGIN_SECONDS,
            )
        except TimeoutError:
            return (
                f"Error: web search timed out after {timeout}s. "
                "Tell the user the web search failed, so you couldn't check recent facts; "
                "don't guess whether recent events happened."
            )
        except Exception as exc:
            return (
                f"Error: web search failed ({exc}). "
                "Tell the user the web search failed, so you couldn't check recent facts; "
                "don't guess whether recent events happened."
            )
        if not results:
            return "No results found."
        return "\n\n".join(
            f"{n}. {r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
            for n, r in enumerate(results, start=1)
        )

    return web_search
