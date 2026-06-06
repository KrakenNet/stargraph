# SPDX-License-Identifier: Apache-2.0
"""FR-1 runtime loop integration -- sequential 3-node graph + ``action.halt``.

End-to-end exercise of :func:`stargraph.graph.loop.execute` with a real
:class:`~stargraph.graph.Graph` / :class:`~stargraph.graph.GraphRun` pair, an
in-memory :class:`~stargraph.checkpoint.sqlite.SQLiteCheckpointer`, and a
minimal stub Fathom adapter that emits a :class:`~stargraph.ir.HaltAction`
on the third tick (``action.halt`` exit per FR-1 / design §3.1.2 step 9).

Asserts (AC-1.1, AC-2.1 -- AC-2.4):

1. State mutates monotonically per node -- ``step_count`` advances
   ``0 -> 1 -> 2 -> 3`` as the loop walks ``n0 -> n1 -> n2`` and
   ``trail`` accumulates the visited ids.
2. The bus emits events in canonical order: three
   :class:`~stargraph.runtime.events.TransitionEvent` (one per node tick)
   followed by exactly one terminal
   :class:`~stargraph.runtime.events.ResultEvent`.
3. The terminal :class:`ResultEvent` carries ``status="done"`` and
   ``final_state`` mirrors the post-merge state (``step_count == 3``,
   ``trail == "n0,n1,n2"``).
4. The terminal :class:`TransitionEvent` from the third tick reports
   ``reason="halt"`` -- the routing decision that ended the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anyio
import pytest

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.ir._models import HaltAction
from stargraph.nodes.base import NodeBase
from stargraph.runtime.events import ResultEvent, TransitionEvent

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Fixture node + stub Fathom                                                  #
# --------------------------------------------------------------------------- #


class _RecordingNode(NodeBase):
    """Append this node's id to ``trail`` and bump ``step_count`` by one.

    Reads ``state.step_count`` / ``state.trail`` defensively via :func:`getattr`
    so the abstract :class:`pydantic.BaseModel` annotation in
    :class:`NodeBase.execute` stays pyright-clean -- the concrete state model
    compiled from the IR ``state_schema`` carries both fields.
    """

    def __init__(self, node_id: str) -> None:
        self._id = node_id

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del ctx
        prev_count: int = getattr(state, "step_count", 0)
        prev_trail: str = getattr(state, "trail", "")
        new_trail = self._id if not prev_trail else f"{prev_trail},{self._id}"
        return {"step_count": prev_count + 1, "trail": new_trail}


class _HaltOnNthFathom:
    """Tiny stub satisfying the :func:`dispatch_node` Fathom call surface.

    Returns ``[]`` from :meth:`mirror_state` so the mirror scheduler is a
    no-op, swallows :meth:`assert_with_provenance` calls (none arrive
    given the empty mirror specs), and emits a single
    :class:`~stargraph.ir.HaltAction` from :meth:`evaluate` once the call
    counter reaches ``halt_after``. The action vocabulary translator
    routes that into :class:`~stargraph.runtime.action.HaltAction`, so the
    loop terminates with ``status="done"`` (FR-1 / §3.1.2 step 9).
    """

    def __init__(self, *, halt_after: int) -> None:
        self._halt_after = halt_after
        self._calls = 0

    def mirror_state(self, state: object, *, annotations: dict[str, Any]) -> list[Any]:
        del state, annotations
        return []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: Any = None,
    ) -> None:
        del template, slots, provenance

    def evaluate(self) -> list[Any]:
        self._calls += 1
        if self._calls >= self._halt_after:
            return [HaltAction(reason="done")]
        return []


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_graph() -> Graph:
    """Build the 3-node sequential IR + compiled Graph used by the test."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:runtime-loop-itest",
        nodes=[
            NodeSpec(id="n0", kind="echo"),
            NodeSpec(id="n1", kind="echo"),
            NodeSpec(id="n2", kind="echo"),
        ],
        state_schema={"step_count": "int", "trail": "str"},
    )
    return Graph(ir)


async def _drain_events(run: GraphRun) -> list[Any]:
    """Drain every event the loop publishes onto ``run.bus`` into a list.

    The bus is single-consumer -- one drainer per run -- and the loop
    publishes the terminal :class:`ResultEvent` last, so receiving until
    a :class:`ResultEvent` arrives is the canonical "done" signal.
    Cancellation safety is not in scope here (the loop drives to a real
    halt action), but a ``move_on_after`` bound keeps the test from
    hanging forever if the event sequence ever regresses.
    """
    received: list[Any] = []
    with anyio.fail_after(5.0):
        while True:
            ev = await run.bus.receive()
            received.append(ev)
            if isinstance(ev, ResultEvent):
                return received


# --------------------------------------------------------------------------- #
# Test                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_sequential_three_node_loop_halts_with_result_event(tmp_path: Path) -> None:
    """3-node graph + ``action.halt`` exit -- the FR-1 acceptance contract."""
    cp = SQLiteCheckpointer(tmp_path / "loop.sqlite")
    await cp.bootstrap()
    try:
        graph = _build_graph()
        initial_state = graph.state_schema(step_count=0, trail="")
        registry: dict[str, NodeBase] = {
            "n0": _RecordingNode("n0"),
            "n1": _RecordingNode("n1"),
            "n2": _RecordingNode("n2"),
        }
        run = GraphRun(
            run_id="run-loop-itest",
            graph=graph,
            initial_state=initial_state,
            node_registry=registry,
            checkpointer=cp,
            fathom=_HaltOnNthFathom(halt_after=3),
        )

        # Drive the run loop and event-bus drainer concurrently. The loop
        # publishes onto ``run.bus`` synchronously inside the tick; with a
        # bounded buffer the producer would block once full, so the
        # drainer task is mandatory (and matches the ``stream()`` contract).
        loop_summary: dict[str, Any] = {}
        events: list[Any] = []

        async def _drive() -> None:
            summary = await run.start()
            loop_summary["summary"] = summary

        async def _drain() -> None:
            drained = await _drain_events(run)
            events.extend(drained)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_drive)
            tg.start_soon(_drain)

        # ---- Assertion 1: state mutated monotonically per node tick. ----
        # The summary itself does not carry final state in v1; the loop's
        # ResultEvent does. Pull it out of the drained event list.
        result_events = [ev for ev in events if isinstance(ev, ResultEvent)]
        assert len(result_events) == 1, (
            f"expected exactly 1 ResultEvent, got {len(result_events)}: {events!r}"
        )
        result = result_events[0]
        assert result.final_state == {"step_count": 3, "trail": "n0,n1,n2"}, (
            f"final_state mismatch: {result.final_state!r}"
        )

        # ---- Assertion 2: events emitted in canonical order. ----
        # 3 TransitionEvent (one per node tick) then 1 ResultEvent.
        assert len(events) == 4, (
            f"expected 4 events (3 transitions + 1 result), got {len(events)}: "
            f"{[type(e).__name__ for e in events]!r}"
        )
        transitions = events[:3]
        assert all(isinstance(ev, TransitionEvent) for ev in transitions), (
            f"first 3 events must be TransitionEvent; got {[type(e).__name__ for e in events]!r}"
        )
        from_seq = [ev.from_node for ev in transitions]
        assert from_seq == ["n0", "n1", "n2"], f"transition from_node sequence wrong: {from_seq!r}"

        # ---- Assertion 3: terminal ResultEvent carries status="done". ----
        assert result.status == "done", f"expected status='done', got {result.status!r}"
        assert result.run_id == "run-loop-itest"
        assert result.run_duration_ms >= 0

        # ---- Assertion 4: third tick routed via halt (action.halt exit). --
        halt_transition = transitions[2]
        assert halt_transition.reason == "halt", (
            f"third tick must report reason='halt' (action.halt exit per FR-1); "
            f"got reason={halt_transition.reason!r}"
        )

        # ---- Assertion 5: lifecycle landed on 'done'. ----
        assert run.state == "done", f"run.state should be 'done', got {run.state!r}"

        # ---- Assertion 6: per-step checkpoints persisted (FR-10). --------
        # State introspection: each node tick wrote a checkpoint; reading the
        # latest must report the post-third-node state.
        latest = await cp.read_latest("run-loop-itest")
        assert latest is not None, "expected a persisted checkpoint after the run"
        assert latest.step == 2, f"latest checkpoint step should be 2, got {latest.step}"
        assert latest.state == {"step_count": 3, "trail": "n0,n1,n2"}, (
            f"checkpointed state mismatch: {latest.state!r}"
        )
        assert latest.last_node == "n2"

        # ---- Assertion 7: summary returned by start() is non-None. -------
        assert "summary" in loop_summary, "run.start() never returned a summary"
        assert loop_summary["summary"].status == "done"
    finally:
        await cp.close()
