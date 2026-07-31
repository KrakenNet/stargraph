# SPDX-License-Identifier: Apache-2.0
"""``std.calculator`` -- AST-safe arithmetic evaluation (no ``eval``).

The expression is parsed with :mod:`ast` and walked against an explicit
allowlist: numeric literals, ``+ - * / // % **``, unary ``+/-``, a fixed
set of :mod:`math` functions, and the constants ``pi`` / ``e`` / ``tau``.
Anything else (names, attributes, subscripts, comprehensions, calls to
unknown functions) is rejected loudly -- there is no code-execution path.

Guards: expression length cap, exponent magnitude cap (blocks
``9**9**9``-style memory bombs), and a finite-result check (``inf`` /
``nan`` would poison downstream JSON serialization).
"""

from __future__ import annotations

import ast
import math
from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["calculator"]

_MAX_EXPR_LEN = 4096
_MAX_EXPONENT = 1000.0

_FUNCS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}

_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _fail(expression: str, why: str) -> StargraphRuntimeError:
    return StargraphRuntimeError(
        f"calculator: {why}",
        expression=expression,
    )


def _eval_node(node: ast.expr, expression: str) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _fail(expression, f"unsupported literal {node.value!r}")
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _eval_node(node.operand, expression)
        return operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, expression)
        right = _eval_node(node.right, expression)
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                if abs(right) > _MAX_EXPONENT:
                    raise _fail(expression, f"exponent magnitude exceeds {_MAX_EXPONENT:g}")
                return left**right
        except ZeroDivisionError as exc:
            raise _fail(expression, "division by zero") from exc
        except OverflowError as exc:
            raise _fail(expression, "result overflows") from exc
        raise _fail(expression, f"unsupported operator {type(node.op).__name__}")
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise _fail(expression, f"unknown name {node.id!r}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise _fail(expression, "only the allowlisted math functions may be called")
        if node.keywords:
            raise _fail(expression, "keyword arguments are not supported")
        args = [_eval_node(arg, expression) for arg in node.args]
        try:
            result = _FUNCS[node.func.id](*args)
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            raise _fail(expression, f"{node.func.id}: {exc}") from exc
        if isinstance(result, bool) or not isinstance(result, (int, float)):
            raise _fail(expression, f"{node.func.id} returned a non-numeric result")
        return result
    raise _fail(expression, f"unsupported syntax {type(node).__name__}")


@tool(
    name="calculator",
    namespace="std",
    version="1",
    side_effects=SideEffects.none,
    description=(
        "Evaluate an arithmetic expression safely (AST allowlist, no eval): "
        "+ - * / // % **, parentheses, abs/round/min/max and common math "
        "functions (sqrt, sin, cos, tan, log, log2, log10, exp, floor, "
        "ceil), constants pi/e/tau."
    ),
)
def calculator(expression: str) -> dict[str, float]:
    """Evaluate an arithmetic expression and return ``{"result": value}``."""
    if len(expression) > _MAX_EXPR_LEN:
        raise _fail(expression[:80] + "...", f"expression exceeds {_MAX_EXPR_LEN} characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise _fail(expression, f"invalid expression: {exc.msg}") from exc
    result = _eval_node(tree.body, expression)
    if not math.isfinite(result):
        raise _fail(expression, "non-finite result")
    return {"result": result}
