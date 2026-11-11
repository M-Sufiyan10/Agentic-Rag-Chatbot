from mcp_utils.units import convert_units


def test_miles_to_km() -> None:
    assert "8.04672" in convert_units(5, "mile", "km")


def test_temperature_offset() -> None:
    assert "212" in convert_units(100, "degC", "degF")


def test_incompatible() -> None:
    assert convert_units(5, "kg", "meter") == "Error: can't convert kg to meter."


def test_unknown_unit() -> None:
    assert convert_units(5, "parsec", "bananas") == "Error: unknown unit 'bananas'."
