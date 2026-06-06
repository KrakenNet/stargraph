# SPDX-License-Identifier: Apache-2.0
"""State-schema load + Mirror introspection (FR-2, FR-13, FR-14, AC-8.5).

Pins the round-trip from IR ``state_schema`` placeholder dict ->
:class:`pydantic.BaseModel` subclass -> Mirror-marker introspection via
:func:`stargraph.ir.mirrored_fields`.

The IR's ``state_schema`` field today is a ``dict[str, str]`` (POC). The
:class:`stargraph.graph.Graph` constructor compiles it to a real Pydantic model;
this file pins the contract that:

* Loading a state schema from IR yields a ``BaseModel`` subclass with all
  declared fields.
* :func:`mirrored_fields` works on the *compiled* state model when a caller
  bolts ``Mirror[T]`` annotations onto a hand-rolled state model (the
  combined surface end users will write in Phase 2).
* Lifecycle round-trip: a model with one field per lifecycle bucket
  (``run`` / ``step`` / ``pinned``) yields three :class:`ResolvedMirror`
  entries, each with the correct ``lifecycle`` literal.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, ConfigDict

from stargraph.graph import Graph
from stargraph.ir import (
    IRDocument,
    Mirror,
    NodeSpec,
    ResolvedMirror,
    mirrored_fields,
)

# ---------------------------------------------------------------------------
# IR state_schema -> compiled BaseModel
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compiled_state_model_round_trips_ir_field_names() -> None:
    """Field names declared in the IR's ``state_schema`` appear verbatim on
    the compiled model."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:state-load",
        nodes=[NodeSpec(id="n", kind="echo")],
        state_schema={"alpha": "str", "beta": "int", "flag": "bool"},
    )
    g = Graph(ir)
    assert set(g.state_schema.model_fields) == {"alpha", "beta", "flag"}


@pytest.mark.unit
def test_compiled_state_model_has_no_mirror_markers_by_default() -> None:
    """The Phase-1 IR ``state_schema`` is a typename dict and carries no
    Mirror metadata; ``mirrored_fields`` returns ``{}``."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:state-load-bare",
        nodes=[NodeSpec(id="n", kind="echo")],
        state_schema={"x": "str"},
    )
    g = Graph(ir)
    assert mirrored_fields(g.state_schema) == {}


# ---------------------------------------------------------------------------
# Mirror introspection (FR-13/FR-14)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mirrored_fields_resolves_all_three_lifecycles() -> None:
    """A model with one field per lifecycle bucket round-trips through
    :func:`mirrored_fields` with the correct ``lifecycle`` on each entry."""

    class State(BaseModel):
        model_config = ConfigDict(extra="forbid")
        per_run: Annotated[str, Mirror(lifecycle="run")]
        per_step: Annotated[int, Mirror(lifecycle="step")]
        per_pinned: Annotated[bool, Mirror(lifecycle="pinned")]

    out = mirrored_fields(State)
    assert out == {
        "per_run": ResolvedMirror(template="per_run", lifecycle="run"),
        "per_step": ResolvedMirror(template="per_step", lifecycle="step"),
        "per_pinned": ResolvedMirror(template="per_pinned", lifecycle="pinned"),
    }


@pytest.mark.unit
def test_mirrored_fields_explicit_template_wins_over_field_name() -> None:
    """An explicit ``Mirror(template=...)`` overrides the FR-13 field-name
    fallback verbatim."""

    class State(BaseModel):
        model_config = ConfigDict(extra="forbid")
        msg: Annotated[str, Mirror(template="message_template", lifecycle="step")]

    out = mirrored_fields(State)
    assert out["msg"].template == "message_template"
    assert out["msg"].lifecycle == "step"


@pytest.mark.unit
def test_mirrored_fields_skips_unmarked_alongside_marked() -> None:
    """A mixed model returns entries only for fields with a ``Mirror`` marker."""

    class State(BaseModel):
        model_config = ConfigDict(extra="forbid")
        marked: Annotated[str, Mirror()]
        unmarked: int
        also_marked: Annotated[bool, Mirror(lifecycle="pinned")]

    out = mirrored_fields(State)
    assert set(out) == {"marked", "also_marked"}
    assert out["marked"].lifecycle == "run"  # default
    assert out["also_marked"].lifecycle == "pinned"


@pytest.mark.unit
def test_mirror_json_schema_round_trip_carries_lifecycle() -> None:
    """AC-8.5: the Mirror lifecycle round-trips through Pydantic's
    ``model_json_schema`` for the JSON-Schema-driven loader to re-attach."""

    class State(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: Annotated[str, Mirror(template="tok", lifecycle="step")]

    schema = State.model_json_schema()
    prop = schema["properties"]["token"]
    assert prop["stargraph_mirror"] is True
    assert prop["stargraph_mirror_template"] == "tok"
    assert prop["stargraph_mirror_lifecycle"] == "step"
