# SPDX-License-Identifier: Apache-2.0
"""``kind: template`` unit tests -- pure render, loud on missing fields."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.registry import build_node_registry
from stargraph.nodes.template import TemplateNode

pytestmark = pytest.mark.unit

_CTX: Any = SimpleNamespace(run_id="run-template")


def _build(**config: object) -> TemplateNode:
    registry = build_node_registry([NodeSpec(id="t", kind="template", config=dict(config))])
    node = registry["t"]
    assert isinstance(node, TemplateNode)
    return node


class _State(BaseModel):
    task: str = "write a parser"
    rationale: str = "missing edge case"
    attempts: int = 2


async def test_renders_fields_into_out() -> None:
    node = _build(template="{task}\n\nFix this feedback: {rationale}", out="task_with_feedback")

    out = await node.execute(_State(), _CTX)

    assert out == {"task_with_feedback": "write a parser\n\nFix this feedback: missing edge case"}


async def test_format_spec_and_non_str_fields_allowed() -> None:
    node = _build(template="attempt {attempts:03d}", out="banner")

    assert await node.execute(_State(), _CTX) == {"banner": "attempt 002"}


async def test_missing_state_field_fails_loud() -> None:
    node = _build(template="{nonexistent}", out="x")

    with pytest.raises(StargraphRuntimeError, match="does not exist on the run state"):
        await node.execute(_State(), _CTX)


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ({}, "invalid config"),  # template + out required
        ({"template": "{a}"}, "invalid config"),
        ({"template": "{a}", "out": "not id"}, "valid identifier"),
        ({"template": "{}", "out": "x"}, "bare state field name"),  # positional
        ({"template": "{a.b}", "out": "x"}, "bare state field name"),  # dotted
        ({"template": "{a[0]}", "out": "x"}, "bare state field name"),  # indexed
        ({"template": "{a}", "out": "x", "extra": 1}, "invalid config"),
    ],
)
def test_build_loud_fail_paths(config: dict[str, Any], match: str) -> None:
    with pytest.raises(IRValidationError, match=match):
        _build(**config)


async def test_literal_braces_escape() -> None:
    node = _build(template="{{literal}} {task}", out="x")

    assert await node.execute(_State(), _CTX) == {"x": "{literal} write a parser"}
