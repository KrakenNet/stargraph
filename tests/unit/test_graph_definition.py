# SPDX-License-Identifier: Apache-2.0
"""Deeper unit tests for :class:`harbor.graph.Graph` (FR-1, FR-2, FR-4).

Pins the FR-1/FR-2 contract surface beyond the smoke coverage in
``tests/unit/test_simulate.py``:

* IR validation is invoked at construction and surfaces the validator's first
  error verbatim (FR-6 force-loud, never silent acceptance).
* ``graph_hash`` is computed once at ``__init__`` and is stable across attribute
  reads (no per-access recomputation that could drift if internal helpers
  changed signature).
* ``runtime_hash`` is independent of ``graph_hash`` -- they have different
  inputs (Python/distribution version vs IR/state-schema/rule-packs) and so
  must never collide for a real IR.
* ``state_schema`` is compiled to a real :class:`pydantic.BaseModel` subclass
  with the IR field types resolved and required-field semantics preserved
  (the dict[str, str] placeholder becomes a typed model, not a dict).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from harbor.errors import IRValidationError
from harbor.errors import ValidationError as HarborValidationError
from harbor.graph import Graph
from harbor.ir import IRDocument, NodeSpec


def _ir(state_schema: dict[str, str] | None = None) -> IRDocument:
    """Two-node IR with optional state_schema override."""
    return IRDocument(
        ir_version="1.0.0",
        id="run:graph-def-test",
        nodes=[
            NodeSpec(id="a", kind="echo"),
            NodeSpec(id="b", kind="echo"),
        ],
        state_schema=state_schema if state_schema is not None else {},
    )


# ---------------------------------------------------------------------------
# IR validation at __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_graph_init_rejects_invalid_node_id_loudly() -> None:
    """A node id that fails stable-id slug validation surfaces from ``__init__``.

    The validator runs on a JSON round-trip (``model_dump(mode="json")``) so an
    already-typed :class:`IRDocument` still gets the structured slug checks.
    The constructor surfaces the first error rather than silently accepting it.
    """
    bad = IRDocument(
        ir_version="1.0.0",
        id="run:graph-def-test",
        nodes=[NodeSpec(id="Not A Slug", kind="echo")],  # spaces forbidden
    )
    with pytest.raises((IRValidationError, HarborValidationError)):
        Graph(bad)


@pytest.mark.unit
def test_graph_init_rejects_set_state_schema_field() -> None:
    """FR-28: ``set``/``frozenset`` state-schema field types are forbidden."""
    with pytest.raises(IRValidationError) as excinfo:
        Graph(_ir(state_schema={"members": "set"}))
    assert excinfo.value.context.get("violation") == "set-field-forbidden"


@pytest.mark.unit
def test_graph_init_rejects_unsupported_state_schema_type() -> None:
    """An unknown type-name in ``state_schema`` raises :class:`ValidationError`."""
    with pytest.raises(HarborValidationError):
        Graph(_ir(state_schema={"x": "not_a_real_type"}))


# ---------------------------------------------------------------------------
# graph_hash + runtime_hash
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_graph_hash_is_stable_across_reads() -> None:
    """``graph_hash`` is a string attr pinned at __init__; subsequent reads
    return the same value byte-for-byte."""
    g = Graph(_ir())
    h1 = g.graph_hash
    h2 = g.graph_hash
    assert h1 == h2
    # sha256 hex digest -> 64 lowercase hex chars.
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


@pytest.mark.unit
def test_graph_hash_distinct_from_runtime_hash() -> None:
    """Structural and runtime hashes are computed from disjoint inputs and
    therefore never coincide for a real IR."""
    g = Graph(_ir())
    assert g.graph_hash != g.runtime_hash
    assert len(g.runtime_hash) == 64


@pytest.mark.unit
def test_graph_hash_changes_when_ir_topology_changes() -> None:
    """Adding a node alters component (a) of the FR-4 hash payload."""
    g1 = Graph(_ir())
    ir2 = IRDocument(
        ir_version="1.0.0",
        id="run:graph-def-test",
        nodes=[
            NodeSpec(id="a", kind="echo"),
            NodeSpec(id="b", kind="echo"),
            NodeSpec(id="c", kind="echo"),
        ],
    )
    g2 = Graph(ir2)
    assert g1.graph_hash != g2.graph_hash


@pytest.mark.unit
def test_graph_hash_changes_when_state_schema_changes() -> None:
    """A state-schema field addition alters component (c) of the hash payload."""
    g1 = Graph(_ir(state_schema={"x": "int"}))
    g2 = Graph(_ir(state_schema={"x": "int", "y": "str"}))
    assert g1.graph_hash != g2.graph_hash


@pytest.mark.unit
def test_runtime_hash_stable_for_same_process() -> None:
    """Two graphs constructed in the same interpreter share ``runtime_hash``
    (it depends only on ``sys.version_info`` + ``harbor.__version__``)."""
    g1 = Graph(_ir())
    g2 = Graph(_ir(state_schema={"x": "int"}))
    assert g1.runtime_hash == g2.runtime_hash


# ---------------------------------------------------------------------------
# state_schema compilation (FR-2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_state_schema_is_basemodel_subclass() -> None:
    """The compiled state schema is a real :class:`BaseModel` subclass, not a
    dict or a stub."""
    g = Graph(_ir(state_schema={"message": "str", "step": "int"}))
    assert isinstance(g.state_schema, type)
    assert issubclass(g.state_schema, BaseModel)


@pytest.mark.unit
def test_state_schema_fields_are_typed_and_required() -> None:
    """Each ``{name: type_name}`` entry becomes a typed required field."""
    g = Graph(_ir(state_schema={"message": "str", "n": "int", "ok": "bool"}))
    fields = g.state_schema.model_fields
    assert set(fields) == {"message", "n", "ok"}
    assert fields["message"].annotation is str
    assert fields["n"].annotation is int
    assert fields["ok"].annotation is bool

    # All fields are required (no defaults supplied) -- omitting a field raises.
    with pytest.raises(ValidationError):
        g.state_schema.model_validate({})  # pyright: ignore[reportUnknownMemberType]

    # Happy path round-trips.
    inst: Any = g.state_schema.model_validate({"message": "hi", "n": 1, "ok": True})
    assert inst.message == "hi"
    assert inst.n == 1
    assert inst.ok is True


@pytest.mark.unit
def test_state_schema_empty_is_legal_stateless_model() -> None:
    """An empty ``state_schema`` yields a model with no fields (legal POC case)."""
    g = Graph(_ir(state_schema={}))
    assert issubclass(g.state_schema, BaseModel)
    assert g.state_schema.model_fields == {}


@pytest.mark.unit
def test_state_schema_class_name_includes_graph_id() -> None:
    """Distinct graph ids produce distinct schema class names so two graphs in
    one process do not share a model class (matters for hash component (c))."""
    g1 = Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:alpha",
            nodes=[NodeSpec(id="n", kind="echo")],
            state_schema={"x": "int"},
        ),
    )
    g2 = Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:beta",
            nodes=[NodeSpec(id="n", kind="echo")],
            state_schema={"x": "int"},
        ),
    )
    assert g1.state_schema.__name__ != g2.state_schema.__name__
    assert "alpha" in g1.state_schema.__name__
    assert "beta" in g2.state_schema.__name__


# ---------------------------------------------------------------------------
# Construction wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_graph_stores_ir_and_optional_wiring() -> None:
    """``ir`` is the validated source; ``plugin_loader``/``registry`` default to
    ``None`` and are stored verbatim when provided."""
    sentinel_loader = object()
    sentinel_registry = object()
    ir = _ir()
    g = Graph(ir, plugin_loader=sentinel_loader, registry=sentinel_registry)
    assert g.ir is ir
    assert g.plugin_loader is sentinel_loader
    assert g.registry is sentinel_registry

    g_default = Graph(ir)
    assert g_default.plugin_loader is None
    assert g_default.registry is None


# ---------------------------------------------------------------------------
# Graph.start construct + return path (T02)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_graph_start_returns_graphrun_with_default_run_id() -> None:
    """``Graph.start(...)`` with ``run_id=None`` mints a fresh ``uuid4().hex``
    and returns a :class:`GraphRun` instance (T02)."""
    from harbor.graph import GraphRun

    g = Graph(_ir())
    run = await g.start(checkpointer=None)
    assert isinstance(run, GraphRun)
    assert isinstance(run.run_id, str)
    assert len(run.run_id) == 32  # uuid4().hex == 32 lowercase hex chars
    assert all(c in "0123456789abcdef" for c in run.run_id)


@pytest.mark.unit
async def test_graph_start_preserves_graph_hash_across_starts() -> None:
    """Two successive ``Graph.start(...)`` calls produce distinct ``GraphRun``
    instances with distinct ``run_id`` but identical ``graph_hash`` (T02)."""
    g = Graph(_ir())
    run_a = await g.start(checkpointer=None)
    run_b = await g.start(checkpointer=None)
    assert run_a is not run_b
    assert run_a.run_id != run_b.run_id
    assert run_a.graph.graph_hash == run_b.graph.graph_hash == g.graph_hash
