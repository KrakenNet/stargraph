# SPDX-License-Identifier: Apache-2.0
"""Rule-routed ``kind: subgraph`` — child IR rules drive an internal loop.

Mounts ``tests/fixtures/cyclic-graph.yaml`` (work -> work -> finish via
mirrored ``phase_verdict`` facts) as a subgraph child. A sequential
subgraph would run each child exactly once (rounds == 1, verdict
``refine``); the routed mode must loop ``work`` twice and only then
route to ``finish`` — proving the child-owned Fathom engine evaluates
live inside the node. Also covers I/O projection maps, the loud
max_steps bound, and the config-without-rules build error.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.registry import build_node_registry
from stargraph.nodes.subgraph import SubGraphNode

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def send(self, event: Any, *, fathom: Any = None) -> None:
        del fathom
        self.events.append(event)


def _ctx(bus: _RecordingBus) -> Any:
    return SimpleNamespace(run_id="run-subgraph", bus=bus, fathom=None)


def _mount(**config: object) -> SubGraphNode:
    spec = NodeSpec(id="inner-loop", kind="subgraph", spec="cyclic-graph.yaml")
    if config:
        spec = spec.model_copy(update={"config": dict(config)})
    registry = build_node_registry([spec], ir_dir=FIXTURES)
    node = registry["inner-loop"]
    assert isinstance(node, SubGraphNode)
    return node


class _ParentState(BaseModel):
    rounds: int = 0
    phase_verdict: str = "pending"
    message: str = ""


async def test_child_rules_loop_then_halt() -> None:
    node = _mount()
    bus = _RecordingBus()

    outputs = await node.execute(_ParentState(), _ctx(bus))

    # Two work rounds prove the goto-work rule fired live; a sequential
    # walk would leave rounds == 1 / verdict == "refine".
    assert outputs == {
        "rounds": 2,
        "phase_verdict": "sufficient",
        "message": "finished after 2 rounds",
    }

    hops = [(e.from_node, e.to_node, e.reason) for e in bus.events]
    assert hops == [
        ("work", "work", "goto"),
        ("work", "finish", "goto"),
        ("finish", "", "halt"),
    ]
    assert all(e.branch_id == "inner-loop" for e in bus.events)
    assert all(e.run_id == "run-subgraph" for e in bus.events)


async def test_transition_events_name_the_rule_that_routed_them() -> None:
    """Each routed transition carries the ``rule_id`` of the defrule behind it.

    The whole chain is under test: ``_ir_builder`` stamps the id into the
    ``stargraph_action`` assert, CLIPS carries it, ``FathomAdapter.evaluate``
    keeps it aligned with the extracted actions, and ``dispatch_node`` recovers
    it for the event. ``cyclic-graph.yaml`` routes through three distinct rules,
    so a positional or last-write-wins bug shows up as a wrong id, not an empty
    one -- which an ``is not None`` assertion would miss.
    """
    node = _mount()
    bus = _RecordingBus()

    await node.execute(_ParentState(), _ctx(bus))

    assert [e.rule_id for e in bus.events] == [
        "r-work-refine",
        "r-work-sufficient",
        "r-finish-halt",
    ]


async def test_projection_maps_rename_fields() -> None:
    class RenamedParent(BaseModel):
        loop_count: int = 5  # must NOT leak into the child (child rounds start 0)
        summary: str = ""

    node = _mount(
        inputs={"message": "summary"},  # child field <- parent field
        outputs={"summary": "message", "loop_count": "rounds"},  # parent <- child
    )
    outputs = await node.execute(RenamedParent(), _ctx(_RecordingBus()))

    # Only mapped fields cross back; child rounds started at its default 0.
    assert outputs == {"summary": "finished after 2 rounds", "loop_count": 2}


async def test_shared_name_projection_skips_unknown_parent_fields() -> None:
    class NarrowParent(BaseModel):
        message: str = ""

    node = _mount()
    outputs = await node.execute(NarrowParent(), _ctx(_RecordingBus()))

    # rounds/phase_verdict have no parent counterpart -> dropped, not leaked.
    assert outputs == {"message": "finished after 2 rounds"}


async def test_max_steps_exceeded_fails_loud() -> None:
    node = _mount(max_steps=1)  # work needs 2 rounds before halt

    with pytest.raises(StargraphRuntimeError, match="exceeded max_steps=1"):
        await node.execute(_ParentState(), _ctx(_RecordingBus()))


async def test_inputs_map_to_missing_parent_field_fails_loud() -> None:
    node = _mount(inputs={"rounds": "no_such_field"})

    with pytest.raises(StargraphRuntimeError, match="does not exist on the run state"):
        await node.execute(_ParentState(), _ctx(_RecordingBus()))


async def test_outputs_map_to_missing_child_field_fails_loud() -> None:
    node = _mount(outputs={"message": "no_such_field"})

    with pytest.raises(StargraphRuntimeError, match="does not exist on the child state"):
        await node.execute(_ParentState(), _ctx(_RecordingBus()))


def test_config_on_ruleless_child_is_ir_error(tmp_path: Path) -> None:
    (tmp_path / "flat.yaml").write_text(
        'ir_version: "1.0.0"\n'
        'id: "graph:flat"\n'
        "state_schema: {message: str}\n"
        "nodes:\n"
        "  - id: only\n"
        "    kind: echo\n",
        encoding="utf-8",
    )
    spec = NodeSpec(id="flat-sub", kind="subgraph", spec="flat.yaml")
    spec = spec.model_copy(update={"config": {"max_steps": 3}})

    with pytest.raises(IRValidationError, match="live-routable rules"):
        build_node_registry([spec], ir_dir=tmp_path)


def test_invalid_config_is_ir_error() -> None:
    spec = NodeSpec(id="inner-loop", kind="subgraph", spec="cyclic-graph.yaml")
    spec = spec.model_copy(update={"config": {"max_steps": 0}})

    with pytest.raises(IRValidationError, match="invalid config"):
        build_node_registry([spec], ir_dir=FIXTURES)
