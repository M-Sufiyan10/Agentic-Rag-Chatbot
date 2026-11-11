"""Tools the agent can call."""

from langchain_core.tools import BaseTool

from app.agent.tools.calculator import calculator
from app.agent.tools.datetime_tool import current_datetime
from app.agent.tools.web_search import make_web_search_tool
from app.config import Settings


def build_tools(settings: Settings) -> list[BaseTool]:
    return [
        calculator,
        current_datetime,
        make_web_search_tool(
            max_results=settings.web_search_max_results,
            timeout=settings.web_search_timeout_seconds,
            backend=settings.web_search_backend,
        ),
    ]
