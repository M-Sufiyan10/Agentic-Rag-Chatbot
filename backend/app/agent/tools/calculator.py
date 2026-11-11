"""Safe arithmetic evaluator exposed to the agent as the `calculator` tool."""

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

Number = int | float

_BINARY_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., Number]] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

_MAX_EXPONENT = 1000
_MAX_BASE_FOR_LARGE_EXPONENT = 1e6
_LARGE_EXPONENT = 100


def _power(base: Number, exponent: Number) -> Number:
    if abs(exponent) > _MAX_EXPONENT or (
        exponent > _LARGE_EXPONENT and abs(base) > _MAX_BASE_FOR_LARGE_EXPONENT
    ):
        raise ValueError("exponent or base is too large")
    result = base**exponent
    if isinstance(result, complex):
        raise ValueError("result is not a real number")
    return result


def _eval(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            return _power(left, right)
        binary = _BINARY_OPS.get(type(node.op))
        if binary is None:
            raise ValueError("unsupported operator")
        return binary(left, right)  # type: ignore[no-any-return]
    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPS.get(type(node.op))
        if unary is None:
            raise ValueError("unsupported operator")
        return unary(_eval(node.operand))  # type: ignore[no-any-return]
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValueError(f"unknown name '{node.id}'")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError("unsupported function")
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        return _FUNCTIONS[node.func.id](*(_eval(arg) for arg in node.args))
    raise ValueError("unsupported expression")


def evaluate(expression: str) -> Number:
    """Evaluate an arithmetic expression. Raises ValueError with a readable message."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        return _eval(tree.body)
    except ZeroDivisionError:
        raise ValueError("division by zero") from None
    except OverflowError:
        raise ValueError("result is too large") from None
    except SyntaxError:
        raise ValueError(f"invalid expression '{expression}'") from None
    except (RecursionError, MemoryError):
        raise ValueError("expression is too deeply nested") from None
    except TypeError as exc:
        raise ValueError(f"invalid arguments ({exc})") from None


@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression exactly. Use this for any arithmetic instead of guessing.

    Supports + - * / // % ** and parentheses, the constants pi and e, and the functions
    sqrt, sin, cos, tan, log, log10, exp, abs, round, floor and ceil.
    Example: "round(sqrt(2) * 3, 2)".
    """
    try:
        return str(evaluate(expression))
    except ValueError as exc:
        return f"Error: {exc}"
