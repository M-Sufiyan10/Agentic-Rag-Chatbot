"""Unit conversion backed by pint."""

import pint

_ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=False)


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    for unit in (from_unit, to_unit):
        try:
            _ureg.Unit(unit)
        except Exception:  # pint raises several error types for bad units
            return f"Error: unknown unit '{unit}'."
    try:
        result = _ureg.Quantity(value, from_unit).to(to_unit)
    except Exception:  # dimensionality or offset-unit errors
        return f"Error: can't convert {from_unit} to {to_unit}."
    return f"{value:g} {from_unit} = {result.magnitude:.6g} {to_unit}"
