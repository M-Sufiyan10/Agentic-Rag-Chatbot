"""Current date and time, exposed to the agent as the `current_datetime` tool."""

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool


def _now(tz: tzinfo) -> datetime:
    return datetime.now(tz)


@tool
def current_datetime(timezone: str | None = None) -> str:
    """Get the current date and time. Use this whenever the answer depends on today's date or time.

    Args:
        timezone: Optional IANA timezone name such as "Europe/London". Defaults to UTC.
    """
    if timezone is None:
        tz: tzinfo = UTC
        name = "UTC"
    else:
        try:
            tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return f"Error: unknown timezone '{timezone}'. Use an IANA name like 'Europe/London'."
        name = timezone
    now = _now(tz)
    return f"{now.isoformat()} ({now:%A}, {name})"
