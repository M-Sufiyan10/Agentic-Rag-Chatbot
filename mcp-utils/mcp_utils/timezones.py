"""Time-zone conversion using the standard library."""

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def convert_time(time_text: str, from_timezone: str, to_timezone: str) -> str:
    source = _zone(from_timezone)
    if source is None:
        return f"Error: unknown timezone '{from_timezone}'. Use an IANA name like 'Asia/Karachi'."
    target = _zone(to_timezone)
    if target is None:
        return f"Error: unknown timezone '{to_timezone}'. Use an IANA name like 'Asia/Karachi'."
    try:
        try:
            moment = datetime.fromisoformat(time_text)
        except ValueError:
            clock = time.fromisoformat(time_text)
            moment = datetime.combine(datetime.now(source).date(), clock)
    except ValueError:
        return f"Error: couldn't read the time '{time_text}'. Use ISO format like 2026-11-11T15:00."
    local = moment.replace(tzinfo=source) if moment.tzinfo is None else moment.astimezone(source)
    converted = local.astimezone(target)
    return (
        f"{local.isoformat(timespec='minutes')} ({from_timezone}) = "
        f"{converted.isoformat(timespec='minutes')} ({to_timezone}, {converted:%A})"
    )
