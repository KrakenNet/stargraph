# SPDX-License-Identifier: Apache-2.0
"""Tests for IRDocument.state_class -- declare a Pydantic BaseModel subclass directly."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from harbor.errors import ValidationError
from harbor.graph import Graph
from harbor.ir._models import IRDocument, NodeSpec


class _MyState(BaseModel):  # pyright: ignore[reportUnusedClass]
    name: str = ""
    items: list[str] = []
    nested: dict[str, int] = {}


@pytest.mark.unit
def test_state_class_imports_and_uses_real_basemodel() -> None:
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-state-class",
        nodes=[NodeSpec(id="n1", kind="echo")],
        state_class="tests.unit.test_state_class:_MyState",
    )
    g = Graph(ir)
    # Identity comparison via the same import path the runtime used --
    # pytest may import this test file under a different module name
    # (e.g. ``unit.test_state_class``) depending on rootdir, so resolve
    # the canonical class through importlib here as well.
    import importlib

    canonical = importlib.import_module("tests.unit.test_state_class")._MyState
    assert g.state_schema is canonical
    assert issubclass(g.state_schema, BaseModel)
    instance = g.state_schema(name="hello", items=["a", "b"], nested={"x": 1})
    assert instance.name == "hello"  # type: ignore[attr-defined]
    assert instance.items == ["a", "b"]  # type: ignore[attr-defined]


@pytest.mark.unit
def test_state_class_and_state_schema_mutually_exclusive() -> None:
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-conflict",
        nodes=[NodeSpec(id="n1", kind="echo")],
        state_class="tests.unit.test_state_class:_MyState",
        state_schema={"foo": "str"},
    )
    with pytest.raises(ValidationError, match=r"state_class.*state_schema"):
        Graph(ir)


@pytest.mark.unit
def test_state_class_not_a_basemodel_raises() -> None:
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-not-basemodel",
        nodes=[NodeSpec(id="n1", kind="echo")],
        state_class="builtins:dict",  # not a BaseModel subclass
    )
    with pytest.raises(ValidationError, match="not a Pydantic BaseModel"):
        Graph(ir)


@pytest.mark.unit
def test_state_class_import_failure_raises() -> None:
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-bad-import",
        nodes=[NodeSpec(id="n1", kind="echo")],
        state_class="harbor.nonexistent.module:Foo",
    )
    with pytest.raises(ValidationError, match="state_class"):
        Graph(ir)


@pytest.mark.unit
def test_state_class_bad_format_raises() -> None:
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-bad-format",
        nodes=[NodeSpec(id="n1", kind="echo")],
        state_class="no_colon_here",
    )
    with pytest.raises(ValidationError, match="state_class"):
        Graph(ir)


@pytest.mark.unit
def test_state_schema_path_unchanged_when_state_class_unset() -> None:
    """Backward compat: graphs without state_class still compile state_schema."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-bc",
        nodes=[NodeSpec(id="n1", kind="echo")],
        state_schema={"name": "str", "n": "int"},
    )
    g = Graph(ir)
    inst = g.state_schema(name="x", n=42)
    assert inst.name == "x"  # type: ignore[attr-defined]
    assert inst.n == 42  # type: ignore[attr-defined]
