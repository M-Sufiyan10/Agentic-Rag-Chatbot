"""FastMCP server (MCPServer in mcp 2.x) exposing the three utility tools."""

from mcp.server.mcpserver import MCPServer

from mcp_utils import timezones, units, weather

mcp = MCPServer("mcp-utils")


@mcp.tool()
async def get_weather(location: str) -> str:
    """Get the current weather for a city or place name (e.g. 'London' or 'Karachi').

    Use this when the user asks about the weather, temperature or conditions somewhere.
    """
    return await weather.get_weather(location)


@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert a number between units of length, mass, temperature, volume, speed and more.

    Use this for questions like "how many km is 5 miles" or "100 degC in degF". Unit names
    are like 'mile', 'km', 'kg', 'degC', 'degF', 'liter'.
    """
    return units.convert_units(value, from_unit, to_unit)


@mcp.tool()
def convert_time(time: str, from_timezone: str, to_timezone: str) -> str:
    """Convert a time from one time zone to another.

    `time` is ISO 8601 ('2026-11-11T15:00') or just a clock time ('15:00', meaning today in
    from_timezone). Zones are IANA names such as 'Asia/Karachi' or 'Europe/London'.
    """
    return timezones.convert_time(time, from_timezone, to_timezone)
