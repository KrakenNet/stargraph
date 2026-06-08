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
import anyio.lowlevel
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


def _build_two_interrupt_graph() -> Graph:
    """interrupt -> interrupt -> passthrough; two sequential HITL pauses."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:two-interrupt-test",
        nodes=[
            NodeSpec(id="gate_one", kind="interrupt"),
            NodeSpec(id="gate_two", kind="interrupt"),
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
    # #68: the failed run records *why* it failed -- a timed-out interrupt
    # is a distinct class from a node error.
    assert run.error_class == "interrupt_timeout"
    assert run.error_cause is not None and "timed out" in run.error_cause


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
async def test_interrupt_no_timeout_parks_awaiting_input(tmp_path: Path) -> None:
    """``timeout=None`` + no respond -> run parks at ``awaiting-input``, no timeout event."""
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

    # Hot-resume (#81): with ``timeout=None`` and no respond, the loop parks
    # indefinitely on the respond event after emitting WaitingForInputEvent.
    # No watchdog -> no InterruptTimeoutEvent; no terminal ResultEvent (the
    # run is alive, awaiting input). ``_drive_until`` cancels the parked
    # drive once it observes the WaitingForInputEvent.
    events = await _drive_until(run, stop_on=WaitingForInputEvent, timeout=2.0)

    assert any(isinstance(ev, WaitingForInputEvent) for ev in events)
    assert not any(isinstance(ev, InterruptTimeoutEvent) for ev in events)
    assert run.state == "awaiting-input"


@pytest.mark.anyio
async def test_interrupt_no_timeout_respond_resumes_to_done(tmp_path: Path) -> None:
    """``timeout=None`` + ``respond()`` -> hot-resume past the interrupt to ``done`` (#81).

    Regression for #81 (HITL run with a timeout-less interrupt hangs at
    ``running`` after respond). The loop parks on the respond event;
    :meth:`GraphRun.respond` sets it, and the *same* live coroutine
    advances through ``next_node`` to a terminal ``done`` + ResultEvent --
    no cold restart, no scheduler re-enqueue.
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "no-timeout-respond.sqlite")
    await checkpointer.bootstrap()

    interrupt_cfg = InterruptNodeConfig(prompt="approve?")  # timeout=None
    graph = _build_two_node_graph()
    run = GraphRun(
        run_id="no-timeout-respond-run",
        graph=graph,
        initial_state=graph.state_schema(message="hello"),
        node_registry={
            "approval_gate": InterruptNode(config=interrupt_cfg),
            "next_node": _PassthroughNode(),
        },
        checkpointer=checkpointer,
    )

    received: list[Any] = []

    async def _drive() -> None:
        await run.start()

    async def _drain() -> None:
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, ResultEvent):
                return

    async def _respond_when_awaiting() -> None:
        deadline = anyio.current_time() + 2.0
        while run.state != "awaiting-input":
            if anyio.current_time() > deadline:
                raise TimeoutError(f"run never reached awaiting-input; state={run.state!r}")
            await anyio.lowlevel.checkpoint()
        await run.respond({"approved": True}, actor="tester")

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            tg.start_soon(_drive)
            tg.start_soon(_respond_when_awaiting)

    # Hot-resume drove the same coroutine past the interrupt to a clean
    # terminal state -- the #81 hang (stuck at ``running`` forever) is gone.
    assert run.state == "done", f"expected resume to terminal 'done'; got {run.state!r}"
    assert any(isinstance(ev, WaitingForInputEvent) for ev in received)
    assert not any(isinstance(ev, InterruptTimeoutEvent) for ev in received)
    results = [ev for ev in received if isinstance(ev, ResultEvent)]
    assert len(results) == 1, f"expected exactly 1 terminal ResultEvent; got {len(results)}"
    assert results[0].status == "done"


@pytest.mark.anyio
async def test_two_sequential_interrupts_each_require_respond(tmp_path: Path) -> None:
    """Two interrupt nodes each park independently until their own respond (#81).

    Regression for the hot-resume respond gate. ``_respond_event`` is an
    ``anyio.Event`` with no ``clear()``, and a single :class:`GraphRun` now
    survives across every interrupt it hits. Without a per-interrupt re-arm
    the first ``respond()`` left the event permanently set, so the *second*
    interrupt's ``wait()`` returned instantly and the run advanced to a
    terminal ``done`` having only ever consumed *one* response (the second
    HITL pause silently skipped).

    The discriminator here is **run completion**, not ``run.state``: on the
    skipped-pause path the loop never flips ``state`` back to ``"running"``
    for the second gate (only :meth:`respond` does that), so ``run.state``
    is left a misleading stale ``"awaiting-input"`` even as the run races to
    completion. We instead assert the run does not reach a terminal
    ``ResultEvent`` until the second ``respond()`` is issued.
    :meth:`GraphRun._rearm_respond_gate` (called by the loop before each
    park) closes the gap.
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "two-interrupt.sqlite")
    await checkpointer.bootstrap()

    cfg = InterruptNodeConfig(prompt="approve?")  # timeout=None on both gates
    graph = _build_two_interrupt_graph()
    run = GraphRun(
        run_id="two-interrupt-run",
        graph=graph,
        initial_state=graph.state_schema(message="hello"),
        node_registry={
            "gate_one": InterruptNode(config=cfg),
            "gate_two": InterruptNode(config=cfg),
            "next_node": _PassthroughNode(),
        },
        checkpointer=checkpointer,
    )

    received: list[Any] = []
    drive_done = False
    skipped_second_gate = False

    async def _drive() -> None:
        nonlocal drive_done
        with contextlib.suppress(BaseException):
            await run.start()
        drive_done = True

    async def _drain() -> None:
        # Drain to a buffer; stop on the terminal ResultEvent (the run's last
        # send, so no later ``bus.send`` can wedge on the abandoned bus).
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, ResultEvent):
                return

    def _waiting_count() -> int:
        return sum(isinstance(ev, WaitingForInputEvent) for ev in received)

    def _result_count() -> int:
        return sum(isinstance(ev, ResultEvent) for ev in received)

    async def _await(predicate: Any, deadline: float, what: str) -> None:
        while not predicate():
            if anyio.current_time() > deadline:
                raise TimeoutError(f"timed out waiting for {what}")
            await anyio.lowlevel.checkpoint()

    async def _responder() -> None:
        nonlocal skipped_second_gate
        deadline = anyio.current_time() + 4.0
        # First interrupt: wait for its WaitingForInputEvent, then respond.
        await _await(lambda: _waiting_count() >= 1, deadline, "first interrupt")
        await run.respond({"approved": True}, actor="tester")
        # Second interrupt reached (its WaitingForInputEvent fired). It MUST
        # block until its own respond. Prove the negative: poll for a window
        # well beyond the run's own I/O latency for a (wrong) terminal
        # completion on a single response. The pre-fix bug skips the second
        # pause and the run finishes here (after the next_node + result
        # checkpoint writes settle, ~tens of ms) on one respond.
        #
        # On detecting the skip, record it and return *without* responding
        # again -- the run is already finishing, so the task group winds down
        # naturally (the drive task is left to complete rather than cancelled
        # mid-checkpoint, which would wedge the aiosqlite worker). The
        # assertion is made after the task group exits.
        await _await(lambda: _waiting_count() >= 2, deadline, "second interrupt")
        budget = anyio.current_time() + 1.5
        while anyio.current_time() < budget:
            if drive_done or _result_count() > 0:
                skipped_second_gate = True
                return
            await anyio.sleep(0.01)
        # Correctly blocked on the second gate -- release it and let the run
        # complete (drain stops on the resulting terminal event).
        await run.respond({"approved": True}, actor="tester")

    with anyio.fail_after(8.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            tg.start_soon(_drive)
            tg.start_soon(_responder)

    assert not skipped_second_gate, (
        "run advanced past the second interrupt on a single respond -- the "
        "respond gate was not re-armed between interrupts"
    )
    assert run.state == "done", f"expected resume to 'done'; got {run.state!r}"
    assert _waiting_count() == 2, f"expected 2 WaitingForInputEvents; got {_waiting_count()}"
    assert not any(isinstance(ev, InterruptTimeoutEvent) for ev in received)
    results = [ev for ev in received if isinstance(ev, ResultEvent)]
    assert len(results) == 1, f"expected exactly 1 terminal ResultEvent; got {len(results)}"
    assert results[0].status == "done"
