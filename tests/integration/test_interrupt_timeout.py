# SPDX-License-Identifier: Apache-2.0
"""TDD: ``InterruptAction.timeout`` background-task wiring (FR-87, NFR-22, AC-14.8).

Pins the loop-side timeout policy for HITL pauses per design §9.5:

1. ``timeout=timedelta(milliseconds=N)`` + no respond -> after roughly
   ``N`` ms (±100ms NFR-22 budget) the loop emits
   :class:`~stargraph.runtime.events.InterruptTimeoutEvent` carrying the
   configured ``on_timeout`` policy.
2. ``on_timeout="halt"`` (the default when only ``timeout`` is set) ->
   the run transitions to ``state="failed"`` and emits a terminal
   :class:`~stargraph.runtime.events.ResultEvent` with ``status="failed"``.
3. ``on_timeout="goto:<node_id>"`` -> the loop resumes execution at the
   named node *as if the InterruptAction were never present*, and the
   run exits cleanly through the normal terminal path.

The tests use a fresh :class:`~stargraph.graph.GraphRun` driven via
``run.start()`` (the same surface :class:`~stargraph.serve.api` uses).
``anyio.fail_after`` bounds the total wall time so a regression that
makes timeout never fire trips the test instead of hanging CI.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.nodes.base import NodeBase
from stargraph.nodes.interrupt import InterruptNode
from stargraph.nodes.interrupt.interrupt_node import InterruptNodeConfig
from stargraph.runtime.events import (
    InterruptTimeoutEvent,
    ResultEvent,
    WaitingForInputEvent,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


pytestmark = pytest.mark.integration


class _PassthroughNode(NodeBase):
    """No-op echo of state -- used as the ``goto:`` target."""

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


def _build_two_node_graph() -> Graph:
    """Interrupt -> passthrough graph; ``passthrough`` is the goto target."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:interrupt-timeout-test",
        nodes=[
            NodeSpec(id="approval_gate", kind="interrupt"),
            NodeSpec(id="next_node", kind="passthrough"),
        ],
        state_schema={"message": "str"},
    )
    return Graph(ir)


async def _drive_until(
    run: GraphRun,
    *,
    stop_on: type | tuple[type, ...],
    timeout: float = 5.0,  # noqa: ASYNC109 -- anyio.fail_after used internally
) -> list[Any]:
    """Drive ``run.start()`` and drain the bus until ``stop_on`` arrives."""
    received: list[Any] = []

    async def _drive() -> None:
        with contextlib.suppress(BaseException):
            await run.start()

    async def _drain(scope: anyio.CancelScope) -> None:
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, stop_on):
                scope.cancel()
                return

    with anyio.fail_after(timeout):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain, tg.cancel_scope)
            tg.start_soon(_drive)
    return received


@pytest.mark.anyio
async def test_interrupt_timeout_halt_emits_timeout_event(tmp_path: Path) -> None:
    """``timeout=100ms`` + ``on_timeout="halt"`` -> InterruptTimeoutEvent + failed."""
    checkpointer = SQLiteCheckpointer(tmp_path / "timeout-halt.sqlite")
    await checkpointer.bootstrap()

    interrupt_cfg = InterruptNodeConfig(
        prompt="approve?",
        timeout=timedelta(milliseconds=100),
        on_timeout="halt",
    )
    graph = _build_two_node_graph()
    run = GraphRun(
        run_id="timeout-halt-run",
        graph=graph,
        initial_state=graph.state_schema(message="hello"),
        node_registry={
            "approval_gate": InterruptNode(config=interrupt_cfg),
            "next_node": _PassthroughNode(),
        },
        checkpointer=checkpointer,
    )

    start = anyio.current_time()
    events = await _drive_until(run, stop_on=ResultEvent, timeout=2.0)
    elapsed = anyio.current_time() - start

    # NFR-22: timeout precision ±100ms. With a 100ms timeout the run
    # should terminate within ~200ms; we give 1.5s of slack to absorb
    # CI scheduler jitter, but the timer itself is bounded by anyio.
    assert elapsed < 1.5, f"timeout did not fire within 1.5s budget: elapsed={elapsed:.3f}s"

    timeout_events = [ev for ev in events if isinstance(ev, InterruptTimeoutEvent)]
    assert len(timeout_events) == 1, f"expected 1 InterruptTimeoutEvent, got {len(timeout_events)}"
    assert timeout_events[0].on_timeout == "halt"

    waiting = [ev for ev in events if isinstance(ev, WaitingForInputEvent)]
    assert len(waiting) == 1, "interrupt boundary must still emit WaitingForInputEvent first"

    results = [ev for ev in events if isinstance(ev, ResultEvent)]
    assert len(results) == 1
    assert results[0].status == "failed"

    assert run.state == "failed"


@pytest.mark.anyio
async def test_interrupt_timeout_goto_resumes_at_named_node(tmp_path: Path) -> None:
    """``on_timeout="goto:next_node"`` -> resume at ``next_node``, exit done."""
    checkpointer = SQLiteCheckpointer(tmp_path / "timeout-goto.sqlite")
    await checkpointer.bootstrap()

    interrupt_cfg = InterruptNodeConfig(
        prompt="approve?",
        timeout=timedelta(milliseconds=100),
        on_timeout="goto:next_node",
    )
    graph = _build_two_node_graph()
    run = GraphRun(
        run_id="timeout-goto-run",
        graph=graph,
        initial_state=graph.state_schema(message="hello"),
        node_registry={
            "approval_gate": InterruptNode(config=interrupt_cfg),
            "next_node": _PassthroughNode(),
        },
        checkpointer=checkpointer,
    )

    events = await _drive_until(run, stop_on=ResultEvent, timeout=2.0)

    timeout_events = [ev for ev in events if isinstance(ev, InterruptTimeoutEvent)]
    assert len(timeout_events) == 1
    assert timeout_events[0].on_timeout == "goto:next_node"

    results = [ev for ev in events if isinstance(ev, ResultEvent)]
    assert len(results) == 1
    # ``goto`` resume runs the named node and the run completes normally.
    assert results[0].status == "done"
    assert run.state == "done"


@pytest.mark.anyio
async def test_interrupt_no_timeout_keeps_cold_restart_contract(tmp_path: Path) -> None:
    """``timeout=None`` -> existing cold-restart behavior, no timeout event."""
    checkpointer = SQLiteCheckpointer(tmp_path / "no-timeout.sqlite")
    await checkpointer.bootstrap()

    interrupt_cfg = InterruptNodeConfig(prompt="approve?")  # no timeout
    graph = _build_two_node_graph()
    run = GraphRun(
        run_id="no-timeout-run",
        graph=graph,
        initial_state=graph.state_schema(message="hello"),
        node_registry={
            "approval_gate": InterruptNode(config=interrupt_cfg),
            "next_node": _PassthroughNode(),
        },
        checkpointer=checkpointer,
    )

    # Without a timeout, the loop exits cold on WaitingForInputEvent (the
    # locked Phase 1 contract). No InterruptTimeoutEvent is emitted; no
    # terminal ResultEvent either (the run is "alive but paused").
    events = await _drive_until(run, stop_on=WaitingForInputEvent, timeout=2.0)

    assert any(isinstance(ev, WaitingForInputEvent) for ev in events)
    assert not any(isinstance(ev, InterruptTimeoutEvent) for ev in events)
    assert run.state == "awaiting-input"
