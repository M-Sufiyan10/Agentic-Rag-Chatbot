from mcp_utils.server import mcp


async def test_lists_three_tools_with_descriptions() -> None:
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {"get_weather", "convert_units", "convert_time"}
    assert all(t.description for t in tools)


async def test_call_convert_units() -> None:
    result = await mcp.call_tool(
        "convert_units", {"value": 5, "from_unit": "mile", "to_unit": "km"}
    )
    assert "8.04672" in str(result)
