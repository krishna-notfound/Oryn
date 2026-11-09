"""calculator: arithmetic via a whitelist AST walk - never eval()."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable

from pydantic import BaseModel, Field

from app.conversation.ports import UserContext
from app.tools.registry import Tool, ToolError

MAX_EXPRESSION_CHARS = 200
MAX_EXPONENT = 100
MAX_MAGNITUDE = 10**100  # keeps every intermediate cheap to compute

type Number = int | float

_BINARY: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Number], Number]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def evaluate(expression: str) -> Number:
    """Evaluate numbers, + - * / // % **, unary +/-, and parentheses. Anything else raises."""
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ToolError("expression is too long")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        return _check(_eval(tree.body))
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("division by zero") from None
    except (SyntaxError, ValueError, RecursionError, MemoryError, OverflowError):
        raise ToolError("not a valid arithmetic expression") from None


def _eval(node: ast.expr) -> Number:
    match node:
        case ast.Constant(value=int() | float() as value) if not isinstance(value, bool):
            return _check(value)
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY:
            return _check(_UNARY[type(op)](_eval(operand)))
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BINARY:
            lhs, rhs = _eval(left), _eval(right)
            if isinstance(op, ast.Pow) and abs(rhs) > MAX_EXPONENT:
                raise ToolError(f"exponent is too large (max {MAX_EXPONENT})")
            return _check(_BINARY[type(op)](lhs, rhs))
        case _:
            raise ToolError("only numbers and + - * / // % ** ( ) are allowed")


def _check(value: Number | complex) -> Number:
    if isinstance(value, complex):  # e.g. (-8) ** 0.5
        raise ToolError("result is not a real number")
    if not math.isfinite(value) or abs(value) > MAX_MAGNITUDE:
        raise ToolError("result is too large")
    return value


def _format(value: Number) -> str:
    return str(value) if isinstance(value, int) else f"{value:.10g}"


class CalculatorArgs(BaseModel):
    expression: str = Field(description="Arithmetic expression, e.g. '(12.5 * 4) / 3'")


class CalculatorTool(Tool[CalculatorArgs]):
    name = "calculator"
    description = (
        "Evaluate an arithmetic expression exactly. Use it instead of doing maths yourself."
    )
    args_model = CalculatorArgs
    timeout_s = 2.0

    async def run(self, args: CalculatorArgs, user: UserContext) -> str:
        return f"{args.expression.strip()} = {_format(evaluate(args.expression))}"
