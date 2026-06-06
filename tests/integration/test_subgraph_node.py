# SPDX-License-Identifier: Apache-2.0
"""FR-7 SubGraphNode integration tests.

Pins the two FR-7 contracts that the parent run cannot verify on its own:

1. **Child events stream on the parent bus.** When the parent run dispatches
   a :class:`SubGraphNode`, every child's :class:`TransitionEvent` is
   published on the parent's :class:`~stargraph.runtime.bus.EventBus`, so the
   downstream consumers (audit log, ``inspect``, replay) see one
   interleaved stream rather than two siblings.
2. **Provenance lineage.** Each child event carries
   ``run_id == parent.run_id`` (the parent's identity propagates -- no
   new ``run_id`` is minted) and ``branch_id == subgraph_id`` (so the
   parent's own events stay distinguishable from the child's).

The tests use lightweight stubs in place of the full :class:`GraphRun`
wiring so the SubGraphNode contract can be exercised in isolation -- the
parent dispatch + checkpoint paths are covered by their own integration
tests under ``tests/integration/``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.subgraph import SubGraphNode
from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import TransitionEvent


class _MessageState(BaseModel):
    """Minimal state model the FR-7 fixture passes through the sub-graph."""

    message: str
    visits: int = 0


class _AppendChild(NodeBase):
    """Fixture child node: appends a tag to ``message`` + bumps ``visits``."""

    id: str

    def __init__(self, *, node_id: str, tag: str) -> None:
        self.id = node_id
        self._tag = tag

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        cur_message: str = getattr(state, "message")  # noqa: B009
        cur_visits: int = getattr(state, "visits")  # noqa: B009
        return {
            "message": f"{cur_message}->{self._tag}",
            "visits": cur_visits + 1,
        }


class _ParentRun:
    """Tiny parent-run stand-in.

    Carries just the surface :class:`SubGraphNode` reads through its
    :class:`SubGraphContext` Protocol (``run_id``, ``bus``, ``fathom``).
    Mirrors the duck-typed-context pattern used elsewhere in
    ``tests/integration/`` so the test does not stand up a full
    :class:`stargraph.graph.GraphRun`.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.bus = EventBus()
        self.fathom: Any = None


# --------------------------------------------------------------------------- #
# Test 1: parent run streams child events.                                    #
# --------------------------------------------------------------------------- #


async def test_parent_run_streams_child_events() -> None:
    """Every child :class:`TransitionEvent` lands on the parent's bus.

    Done-when (test contract): after ``await sub.execute(...)`` the
    parent's event bus drains exactly ``len(children)`` events, all of
    type ``transition`` and all carrying the sub-graph's ``branch_id``.
    """
    parent = _ParentRun(run_id="parent-run-7")
    sub = SubGraphNode(
        subgraph_id="train_subgraph",
        children=[
            _AppendChild(node_id="prep", tag="P"),
            _AppendChild(node_id="fit", tag="F"),
            _AppendChild(node_id="register", tag="R"),
        ],
    )

    initial = _MessageState(message="seed", visits=0)
    outputs = await sub.execute(initial, parent)  # type: ignore[arg-type]

    # Last-write-wins: every child wrote `message` + `visits`, so the
    # accumulated dict reflects the final child's state.
    assert outputs["message"] == "seed->P->F->R"
    assert outputs["visits"] == 3

    # Drain the parent bus -- exactly one TransitionEvent per child.
    drained: list[TransitionEvent] = []
    for _ in range(3):
        ev = await parent.bus.receive()
        assert isinstance(ev, TransitionEvent)
        drained.append(ev)

    assert [e.from_node for e in drained] == ["prep", "fit", "register"]
    # The terminal child has no successor; convention matches the
    # parent dispatch loop (`to_node = ""`).
    assert [e.to_node for e in drained] == ["fit", "register", ""]
    assert all(e.reason == "subgraph" for e in drained)


# --------------------------------------------------------------------------- #
# Test 2: provenance lineage -- parent run_id propagates to child events.     #
# --------------------------------------------------------------------------- #


async def test_provenance_lineage_propagates_parent_run_id() -> None:
    """Every child event carries the parent's ``run_id`` + sub's ``branch_id``.

    Provenance lineage assertion: a downstream audit consumer receiving
    just the child events must be able to reconstruct
    ``(parent_run_id, subgraph_id, child_id)`` from each event alone --
    no out-of-band correlation.
    """
    parent_run_id = "parent-run-FR7-lineage"
    sub_id = "subgraph-A"

    parent = _ParentRun(run_id=parent_run_id)
    sub = SubGraphNode(
        subgraph_id=sub_id,
        children=[
            _AppendChild(node_id="alpha", tag="a"),
            _AppendChild(node_id="beta", tag="b"),
        ],
    )

    await sub.execute(_MessageState(message="x", visits=0), parent)  # type: ignore[arg-type]

    received: list[TransitionEvent] = []
    for _ in range(2):
        ev = await parent.bus.receive()
        assert isinstance(ev, TransitionEvent)
        received.append(ev)

    # Parent run_id propagates verbatim to every child event.
    assert all(e.run_id == parent_run_id for e in received), (
        "child events did not carry the parent's run_id -- provenance lineage broken"
    )
    # branch_id discriminates the sub-graph; never None on child events.
    assert all(e.branch_id == sub_id for e in received), (
        "child events did not carry the sub-graph's branch_id -- lineage discriminator missing"
    )


# --------------------------------------------------------------------------- #
# Test 3: empty sub-graph is a no-op (degenerate but legal).                  #
# --------------------------------------------------------------------------- #


async def test_empty_subgraph_emits_no_events_and_returns_empty_outputs() -> None:
    """A sub-graph with zero children is legal: no events, no merges."""
    parent = _ParentRun(run_id="parent-empty")
    sub = SubGraphNode(subgraph_id="empty", children=[])

    outputs = await sub.execute(_MessageState(message="seed"), parent)  # type: ignore[arg-type]

    assert outputs == {}


# --------------------------------------------------------------------------- #
# Test 4: missing bus surfaces loudly (FR-6 force-loud).                      #
# --------------------------------------------------------------------------- #


class _BareCtx:
    """Context with only ``run_id`` (the Phase-1 minimum) -- no ``bus``."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


async def test_missing_bus_on_context_raises_loudly() -> None:
    """A context lacking ``bus`` is a wiring bug; FR-6 forbids silent drop."""
    sub = SubGraphNode(
        subgraph_id="x",
        children=[_AppendChild(node_id="only", tag="o")],
    )

    import pytest

    with pytest.raises(AttributeError):
        await sub.execute(_MessageState(message="m"), _BareCtx("r"))  # type: ignore[arg-type]
