# SPDX-License-Identifier: Apache-2.0
"""``std.calculator`` -- AST allowlist evaluation, guard rails, loud failures."""

from __future__ import annotations

import math

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.std.calculator import calculator

pytestmark = pytest.mark.unit


def test_arithmetic_precedence() -> None:
    assert calculator(expression="2 + 3 * 4") == {"result": 14}


def test_parentheses_and_power() -> None:
    assert calculator(expression="(2 + 3) ** 2") == {"result": 25}


def test_functions_and_constants() -> None:
    assert calculator(expression="sqrt(16) + abs(-2)") == {"result": 6.0}
    assert abs(calculator(expression="cos(0) + pi")["result"] - (1 + math.pi)) < 1e-9


def test_unary_and_modulo() -> None:
    assert calculator(expression="-7 % 3") == {"result": 2}
    assert calculator(expression="7 // 2") == {"result": 3}


def test_division_by_zero_is_loud() -> None:
    with pytest.raises(StargraphRuntimeError, match="division by zero"):
        calculator(expression="1 / 0")


def test_unknown_function_rejected() -> None:
    with pytest.raises(StargraphRuntimeError, match="allowlisted math functions"):
        calculator(expression="__import__('os')")


def test_attribute_access_rejected() -> None:
    # The string is hostile INPUT the calculator must refuse to evaluate;
    # nothing here executes os.system.
    with pytest.raises(StargraphRuntimeError, match="allowlisted math functions"):
        calculator(expression="os.system('true')")


def test_unknown_name_rejected() -> None:
    with pytest.raises(StargraphRuntimeError, match="unknown name"):
        calculator(expression="x + 1")


def test_string_literal_rejected() -> None:
    with pytest.raises(StargraphRuntimeError, match="unsupported literal"):
        calculator(expression="'a' * 3")


def test_exponent_bomb_guard() -> None:
    with pytest.raises(StargraphRuntimeError, match="exponent magnitude"):
        calculator(expression="9 ** 99999")


def test_non_finite_result_rejected() -> None:
    with pytest.raises(StargraphRuntimeError, match="non-finite"):
        calculator(expression="1e308 * 10")


def test_syntax_error_is_loud() -> None:
    with pytest.raises(StargraphRuntimeError, match="invalid expression"):
        calculator(expression="2 +")


def test_expression_length_cap() -> None:
    with pytest.raises(StargraphRuntimeError, match="exceeds"):
        calculator(expression="1+" * 3000 + "1")
