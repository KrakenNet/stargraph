# SPDX-License-Identifier: Apache-2.0
"""Deeper unit tests for :class:`stargraph.graph.GraphRun` (FR-1, design §3.1.1/§3.1.3).

Pins the GraphRun lifecycle + single-use invariants without driving the full
run loop (which is exercised end-to-end by integration tests):

* Lifecycle states match design §3.1.3 (``pending``/``running``/``paused``/
  ``done``/``failed``); a fresh run starts ``pending``.
* :meth:`GraphRun.start` and :meth:`GraphRun.wait` refuse to drive a run that
  is not ``pending`` -- single-use enforcement (Open Q3 invariant).
* Identity attributes (``run_id``, parent ``graph``, ``parent_run_id``) are
  stored verbatim; per-run primitives (``bus``, ``mirror_scheduler``) are
  fresh on each construction (no cross-run sharing).
"""

from __future__ import annotations

from typing import get_args

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.graph import Graph, GraphRun, RunState
from stargraph.ir import IRDocument, NodeSpec
from stargraph.runtime.bus import EventBus
from stargraph.runtime.mirror_lifecycle import MirrorScheduler


def _graph() -> Graph:
    return Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:graph-run-test",
            nodes=[NodeSpec(id="a", kind="echo")],
        ),
    )


# ---------------------------------------------------------------------------
# RunState literal contract (design §3.1.3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_runstate_literal_covers_all_lifecycle_states() -> None:
    """``RunState`` exposes the design §3.1.3 + HITL/cancel/pause states.

    The set widens beyond the original five (``pending``/``running``/``paused``/
    ``done``/``failed``) to include the HITL gate (``awaiting-input``), the
    cooperative cancellation terminal (``cancelled``), and the unified failure
    name (``error``). ``failed`` remains as a transitional alias scheduled for
    Phase-2 cleanup -- see comment block on ``RunState`` in ``graph/run.py``.
    """
    assert set(get_args(RunState)) == {
        "pending",
        "running",
        "paused",
        "awaiting-input",
        "done",
        "cancelled",
        "error",
        "failed",
    }


# ---------------------------------------------------------------------------
# Construction defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fresh_run_starts_in_pending_state() -> None:
    """A freshly-constructed :class:`GraphRun` is in ``pending`` until the
    run loop transitions it (loop owns transitions, not callers)."""
    g = _graph()
    run = GraphRun(run_id="run-1", graph=g)
    assert run.state == "pending"


@pytest.mark.unit
def test_run_construction_pins_identity_attrs() -> None:
    """``run_id``, parent ``graph``, ``parent_run_id`` are stored verbatim."""
    g = _graph()
    run = GraphRun(run_id="run-2", graph=g, parent_run_id="parent-1")
    assert run.run_id == "run-2"
    assert run.graph is g
    assert run.parent_run_id == "parent-1"


@pytest.mark.unit
def test_run_construction_defaults_optional_wiring() -> None:
    """Optional wiring (checkpointer/capabilities/fathom/initial_state/registry)
    defaults so test-only handles can omit them."""
    run = GraphRun(run_id="run-3", graph=_graph())
    assert run.parent_run_id is None
    assert run.initial_state is None
    assert run.node_registry == {}
    assert run.checkpointer is None
    assert run.capabilities is None
    assert run.fathom is None


@pytest.mark.unit
def test_run_construction_creates_fresh_bus_and_scheduler() -> None:
    """Each run owns its own :class:`EventBus` + :class:`MirrorScheduler`
    instances (no cross-run sharing -- Open Q3 single-use invariant)."""
    g = _graph()
    r1 = GraphRun(run_id="run-a", graph=g)
    r2 = GraphRun(run_id="run-b", graph=g)
    assert isinstance(r1.bus, EventBus)
    assert isinstance(r1.mirror_scheduler, MirrorScheduler)
    assert r1.bus is not r2.bus
    assert r1.mirror_scheduler is not r2.mirror_scheduler


# ---------------------------------------------------------------------------
# Single-use enforcement (Open Q3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("terminal_state", ["done", "failed", "paused", "running"])
async def test_start_refuses_non_pending_run(terminal_state: RunState) -> None:
    """``start()`` raises :class:`StargraphRuntimeError` on any non-``pending`` state.

    Pins single-use semantics: a completed/failed/paused/in-flight run cannot
    be re-driven via ``start()`` (callers must build a new ``GraphRun`` or use
    :meth:`resume`).
    """
    run = GraphRun(run_id="run-restart", graph=_graph())
    run.state = terminal_state  # simulate post-loop transition
    with pytest.raises(StargraphRuntimeError) as excinfo:
        await run.start()
    assert "pending" in str(excinfo.value)
    assert terminal_state in str(excinfo.value)


@pytest.mark.unit
@pytest.mark.parametrize("terminal_state", ["done", "failed", "paused", "running"])
async def test_wait_refuses_non_pending_run(terminal_state: RunState) -> None:
    """``wait()`` enforces the same single-use invariant as ``start()``."""
    run = GraphRun(run_id="run-wait-restart", graph=_graph())
    run.state = terminal_state
    with pytest.raises(StargraphRuntimeError):
        await run.wait()


@pytest.mark.unit
async def test_stream_is_async_generator_yielding_nothing_in_skeleton() -> None:
    """The Phase-1 ``stream()`` body yields no events but is a real async
    iterator -- callers can ``async for`` without runtime errors."""
    run = GraphRun(run_id="run-stream", graph=_graph())
    events: list[object] = []
    async for ev in run.stream():
        events.append(ev)
    assert events == []


@pytest.mark.unit
def test_checkpoint_returns_checkpoint_model_with_all_required_fields() -> None:
    """``checkpoint()`` returns a :class:`Checkpoint` with every required field
    populated per ``checkpoint/protocol.py:34-63`` (INV-2).

    Pinned via Pydantic re-construction: ``Checkpoint(**result.model_dump())``
    raises if any required field is missing or wrong-typed, so the assertion
    self-updates as the protocol model evolves. T01.
    """
    from stargraph.checkpoint.protocol import Checkpoint

    run = GraphRun(run_id="run-ckpt", graph=_graph())
    result = run.checkpoint()
    assert isinstance(result, Checkpoint)
    # Round-trip through model_dump → Checkpoint(**...) so Pydantic raises
    # on missing required fields. Self-updating against schema evolution.
    Checkpoint(**result.model_dump())


@pytest.mark.unit
def test_checkpoint_preserves_graph_hash_across_successive_calls() -> None:
    """Two ``checkpoint()`` calls on an unchanged run return identical
    ``graph_hash`` -- INV-1 reproducibility under T01."""
    run = GraphRun(run_id="run-ckpt-stable", graph=_graph())
    cp1 = run.checkpoint()
    cp2 = run.checkpoint()
    assert cp1.graph_hash == cp2.graph_hash


@pytest.mark.unit
def test_graph_run_init_accepts_fact_store_and_audit_sink_kwargs() -> None:
    """T01 extends ``GraphRun.__init__`` with ``fact_store`` and
    ``audit_sink`` optional kwargs (default ``None``, stored on self)."""
    run = GraphRun(run_id="run-extras", graph=_graph())
    # Default-None pins back-compat for legacy callers.
    assert run.fact_store is None
    assert run.audit_sink is None
    # Explicit-pass round-trip pins attribute storage shape.
    sentinel_fs = object()
    sentinel_as = object()
    run2 = GraphRun(
        run_id="run-extras-set",
        graph=_graph(),
        fact_store=sentinel_fs,
        audit_sink=sentinel_as,
    )
    assert run2.fact_store is sentinel_fs
    assert run2.audit_sink is sentinel_as
