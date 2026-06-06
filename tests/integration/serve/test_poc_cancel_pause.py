# SPDX-License-Identifier: Apache-2.0
"""POC integration smoke -- cancel + pause emit correct events.

Drives a deliberately long-running stub graph through the FastAPI serve
surface (:func:`stargraph.serve.api.create_app`) and exercises the two
cooperative-lifecycle HTTP routes:

* ``POST /v1/runs/{run_id}/pause`` -> :func:`stargraph.serve.lifecycle.pause_run`
  -> :meth:`stargraph.graph.GraphRun.pause` -> bus emits
  :class:`~stargraph.runtime.events.RunPausedEvent`. The loop's cooperative
  pause boundary (``loop.py`` lines 240-248, task 1.8) observes
  ``_pause_event`` after the next :func:`dispatch_node` returns,
  transitions ``state="paused"``, emits a terminal :class:`ResultEvent`
  with ``status="paused"``, and exits cleanly.
* ``POST /v1/runs/{run_id}/cancel`` -> :func:`stargraph.serve.lifecycle.cancel_run`
  -> :meth:`stargraph.graph.GraphRun.cancel` -> bus emits
  :class:`~stargraph.runtime.events.RunCancelledEvent`. The loop's
  cooperative cancel boundary raises :class:`asyncio.CancelledError` at
  the next checkpoint boundary; ``state`` is set to ``"cancelled"`` by
  ``cancel()`` itself before the loop ever sees the signal (no terminal
  ``ResultEvent`` -- ``ResultEvent.status`` does not include
  ``"cancelled"``).

Fixture shape: a 12-node graph of :class:`_LongRunningStubNode`, each
sleeping ~0.05s before returning an empty patch. Total cooperative
budget is ~0.6s -- comfortably wider than the test's ~0.1s window
between observing the first :class:`TransitionEvent` and issuing the
HTTP cancel/pause request, so the boundary fires while the loop is
still walking the graph (rather than after a natural ``done``
termination). The 0.05s per-node tick is well below NFR-17's 5s p95
budget; the test asserts the boundary fires within 2s for the POC
(stricter than the production NFR because the fixture is faster).

Mirrors task 1.30's harness (commit 3677f30):
:class:`httpx.AsyncClient(transport=httpx.ASGITransport(app=...))`,
``app.state.deps["runs"]`` + ``["broadcasters"]`` registration, direct
:class:`GraphRun` spawn (no Scheduler stub -- the cancel/pause routes
read the registry, not the scheduler).

POC scope held: timing assertion is ``<= 2s`` (not the 5s production
NFR-17); audit-fact persistence is best-effort (audit-sink wiring lands
in task 2.30); ``cancelled`` state folds onto the
:class:`Checkpointer.RunSummary` Literal as ``"failed"`` per the
task-1.22 mapping (the Phase-2 widening of the Literal lands with
task 1.2 backfill).

Refs: tasks.md §1.31; design §4.1 (state machine), §4.2 (cancel/pause
loop semantics); FR-76, FR-77, FR-79, AC-13.4, AC-13.5; NFR-17, NFR-18.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio
import anyio.lowlevel
import httpx
import pytest

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.nodes.base import NodeBase
from stargraph.runtime.events import (
    ResultEvent,
    RunCancelledEvent,
    RunPausedEvent,
    TransitionEvent,
)
from stargraph.serve.api import create_app
from stargraph.serve.broadcast import EventBroadcaster
from stargraph.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


pytestmark = pytest.mark.serve


# --------------------------------------------------------------------------- #
# Fixture node                                                                #
# --------------------------------------------------------------------------- #


class _LongRunningStubNode(NodeBase):
    """Cooperative-yield stub node -- sleeps ``tick_seconds`` per execute.

    Each ``execute`` call awaits :func:`anyio.sleep` for ``tick_seconds``
    so the loop stretches over a wider time window. ``anyio.sleep``
    yields cooperatively to the scheduler, letting concurrent HTTP
    requests against the same FastAPI app drive lifecycle methods on
    the live :class:`GraphRun` between ticks.

    The node returns an empty patch -- state is unchanged, only time
    passes. The 12-node graph chains 12 of these together; the loop's
    cancel/pause boundary observes the cooperative signal at any
    inter-node checkpoint boundary.
    """

    def __init__(self, *, tick_seconds: float = 0.05) -> None:
        self.tick_seconds = tick_seconds

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        await anyio.sleep(self.tick_seconds)
        return {}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


_NUM_NODES = 12
_TICK_SECONDS = 0.05
_BOUNDARY_BUDGET_SECONDS = 2.0


def _build_long_running_graph() -> Graph:
    """Build a 12-node IR + compiled :class:`Graph` for the cancel/pause fixture.

    All 12 nodes share the ``stub`` kind; the loop walks them in IR
    order via :func:`stargraph.runtime.dispatch.dispatch_node`'s
    ``ContinueAction`` fallthrough (no Fathom, no ``goto``). The
    state schema carries a single ``counter`` field that nodes leave
    untouched -- it exists only because :class:`Graph` requires a
    non-empty schema for the compiled :class:`pydantic.BaseModel`.
    """
    nodes = [NodeSpec(id=f"step_{i}", kind="stub") for i in range(_NUM_NODES)]
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:poc-cancel-pause",
        nodes=nodes,
        state_schema={"counter": "int"},
    )
    return Graph(ir)


def _build_node_registry() -> dict[str, NodeBase]:
    """Map every node id in the graph to a fresh :class:`_LongRunningStubNode`."""
    return {
        f"step_{i}": _LongRunningStubNode(tick_seconds=_TICK_SECONDS) for i in range(_NUM_NODES)
    }


async def _wait_for_running(
    run: GraphRun,
    *,
    timeout: float = 1.0,  # noqa: ASYNC109 -- anyio.fail_after wraps the call site
) -> None:
    """Poll until ``run.state == 'running'`` (or raise :class:`TimeoutError`).

    The loop sets ``state="running"`` at line 193 of
    :func:`stargraph.graph.loop.execute`, before the first
    :func:`dispatch_node` call. The polling interval is one
    :func:`anyio.lowlevel.checkpoint` yield so we observe the transition
    at the earliest possible scheduler boundary. Used by both scenarios
    to gate the HTTP request on the run actually being live
    (``GraphRun.cancel`` requires
    ``state in ('running', 'paused', 'awaiting-input')`` and
    ``GraphRun.pause`` requires ``state == 'running'``).
    """
    deadline = anyio.current_time() + timeout
    while run.state != "running":
        if anyio.current_time() > deadline:
            raise TimeoutError(
                f"run {run.run_id!r} did not reach 'running' within {timeout}s "
                f"(state={run.state!r})"
            )
        await anyio.lowlevel.checkpoint()


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.integration
async def test_poc_pause_emits_run_paused_event(tmp_path: Path) -> None:
    """Pause a live run via the HTTP route; assert :class:`RunPausedEvent` lands.

    Asserts:

    1. ``POST /v1/runs/{id}/pause`` returns a structured 200 with a JSON
       :class:`RunSummary` body.
    2. The bus emits exactly one :class:`RunPausedEvent` carrying the
       configured ``actor`` (the bypass auth provider supplies
       ``"anonymous"``).
    3. After the loop's pause boundary fires, ``run.state == "paused"``
       and a terminal :class:`ResultEvent` with ``status="paused"`` is
       emitted.
    4. The boundary fires within :data:`_BOUNDARY_BUDGET_SECONDS` (POC
       fixture; the production NFR-18 budget is wider).
    """
    # --- Phase 0: build run + register on app.state.deps -------------------
    checkpointer = SQLiteCheckpointer(tmp_path / "pause.sqlite")
    await checkpointer.bootstrap()

    graph = _build_long_running_graph()
    initial_state = graph.state_schema(counter=0)
    run_id = "poc-cancel-pause-run-pause"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=initial_state,
        node_registry=_build_node_registry(),
        checkpointer=checkpointer,
    )

    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    # --- Phase 1: drive run + drain bus + issue HTTP pause concurrently ----
    received: list[Any] = []
    boundary_seen_at: list[float] = []
    started_at = anyio.current_time()

    async def _drive() -> None:
        # ``run.start()`` returns cleanly on pause (loop's pause arm
        # transitions state + emits ResultEvent + returns _summary).
        await run.start()

    async def _drain() -> None:
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, ResultEvent):
                # Terminal pause boundary observed; record timing and exit
                # the drainer. The drive task's ``run.start()`` returns
                # right after this event lands.
                boundary_seen_at.append(anyio.current_time())
                return

    async def _issue_pause() -> None:
        # Wait for the loop to hit ``state='running'`` so ``pause()`` does
        # not raise ``StargraphRuntimeError("cannot pause from state ...")``.
        await _wait_for_running(run)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/v1/runs/{run_id}/pause")
        # ---- Assertion 1: structured 200 with JSON body.
        assert response.status_code == 200, (
            f"expected 200; got {response.status_code} body={response.text!r}"
        )
        body = response.json()
        assert "status" in body, f"pause body missing 'status': {body!r}"
        # The summary may report ``running`` if the loop has not yet
        # observed the pause signal (per :func:`pause_run` docstring);
        # accept either ``running`` or ``paused``.
        assert body["status"] in ("running", "paused"), f"unexpected pause body status: {body!r}"

    with anyio.fail_after(_BOUNDARY_BUDGET_SECONDS + 1.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            tg.start_soon(_drive)
            tg.start_soon(_issue_pause)

    # ---- Assertion 2: exactly one RunPausedEvent emitted on the bus.
    paused_events = [ev for ev in received if isinstance(ev, RunPausedEvent)]
    assert len(paused_events) == 1, (
        f"expected exactly 1 RunPausedEvent, got {len(paused_events)}; "
        f"events: {[type(e).__name__ for e in received]!r}"
    )
    assert paused_events[0].actor == "anonymous", (
        f"unexpected RunPausedEvent.actor: {paused_events[0].actor!r}"
    )

    # ---- Assertion 3: terminal state + ResultEvent(status='paused').
    assert run.state == "paused", f"run.state after pause boundary: {run.state!r}"
    result_events = [ev for ev in received if isinstance(ev, ResultEvent)]
    assert len(result_events) == 1, (
        f"expected exactly 1 terminal ResultEvent, got {len(result_events)}; "
        f"events: {[type(e).__name__ for e in received]!r}"
    )
    assert result_events[0].status == "paused", (
        f"expected ResultEvent.status='paused', got {result_events[0].status!r}"
    )

    # ---- Assertion 4: boundary fired within the POC budget.
    assert boundary_seen_at, "drainer did not observe a terminal ResultEvent"
    elapsed = boundary_seen_at[0] - started_at
    assert elapsed <= _BOUNDARY_BUDGET_SECONDS, (
        f"pause boundary took {elapsed:.3f}s > {_BOUNDARY_BUDGET_SECONDS}s budget"
    )

    # Sanity: the loop got at least one node tick in before pausing
    # (i.e. the boundary fired post-first-dispatch, not pre-loop).
    transitions = [ev for ev in received if isinstance(ev, TransitionEvent)]
    assert transitions, (
        "expected at least one TransitionEvent before pause boundary; "
        "loop may have exited before observing the pause signal"
    )


@pytest.mark.serve
@pytest.mark.integration
async def test_poc_cancel_emits_run_cancelled_event(tmp_path: Path) -> None:
    """Cancel a live run via the HTTP route; assert :class:`RunCancelledEvent` lands.

    Asserts:

    1. ``POST /v1/runs/{id}/cancel`` returns a structured 200 with a JSON
       :class:`RunSummary` body.
    2. The bus emits exactly one :class:`RunCancelledEvent` carrying the
       configured ``actor`` and ``reason="user"``.
    3. After the loop's cancel boundary fires, ``run.state == "cancelled"``.
    4. The boundary fires within :data:`_BOUNDARY_BUDGET_SECONDS`.

    Note: cancel does **not** emit a terminal :class:`ResultEvent` --
    the loop's ``except asyncio.CancelledError`` arm re-raises rather
    than emitting a duplicate terminal event (``ResultEvent.status``
    does not include ``"cancelled"``; see ``loop.py`` lines 277-288).
    The drive task therefore exits via :class:`CancelledError`
    propagation, which is suppressed at the task-group boundary.
    """
    # --- Phase 0: build run + register on app.state.deps -------------------
    checkpointer = SQLiteCheckpointer(tmp_path / "cancel.sqlite")
    await checkpointer.bootstrap()

    graph = _build_long_running_graph()
    initial_state = graph.state_schema(counter=0)
    run_id = "poc-cancel-pause-run-cancel"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=initial_state,
        node_registry=_build_node_registry(),
        checkpointer=checkpointer,
    )

    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    # --- Phase 1: drive run + drain bus + issue HTTP cancel concurrently ---
    received: list[Any] = []
    boundary_seen_at: list[float] = []
    started_at = anyio.current_time()

    async def _drive() -> None:
        # ``run.start()`` raises :class:`asyncio.CancelledError` on
        # cooperative cancel; the loop's ``except CancelledError: raise``
        # arm propagates the signal so callers per NFR-17 see the
        # cancellation. Suppress at this level so the task group does
        # not abort siblings (the drainer + the HTTP-issuer have their
        # own completion conditions).
        try:
            await run.start()
        except asyncio.CancelledError:
            return

    async def _drain() -> None:
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, RunCancelledEvent):
                # Cancel boundary observed; the loop's CancelledError
                # propagation may take an extra scheduler tick to land,
                # so record the bus-side observation time as the
                # canonical cancel boundary.
                boundary_seen_at.append(anyio.current_time())
                return

    async def _issue_cancel() -> None:
        await _wait_for_running(run)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/v1/runs/{run_id}/cancel")
        # ---- Assertion 1: structured 200 with JSON body.
        assert response.status_code == 200, (
            f"expected 200; got {response.status_code} body={response.text!r}"
        )
        body = response.json()
        assert "status" in body, f"cancel body missing 'status': {body!r}"
        # ``cancel()`` set ``state='cancelled'`` synchronously; the
        # serve-layer status-lattice fold maps ``cancelled -> failed``
        # (task 1.22 mapping; Phase 2 widens the Literal).
        assert body["status"] == "failed", f"unexpected cancel body status: {body!r}"

    with anyio.fail_after(_BOUNDARY_BUDGET_SECONDS + 1.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            tg.start_soon(_drive)
            tg.start_soon(_issue_cancel)

    # ---- Assertion 2: exactly one RunCancelledEvent emitted on the bus.
    cancelled_events = [ev for ev in received if isinstance(ev, RunCancelledEvent)]
    assert len(cancelled_events) == 1, (
        f"expected exactly 1 RunCancelledEvent, got {len(cancelled_events)}; "
        f"events: {[type(e).__name__ for e in received]!r}"
    )
    assert cancelled_events[0].actor == "anonymous", (
        f"unexpected RunCancelledEvent.actor: {cancelled_events[0].actor!r}"
    )
    assert cancelled_events[0].reason == "user", (
        f"unexpected RunCancelledEvent.reason: {cancelled_events[0].reason!r}"
    )

    # ---- Assertion 3: terminal cancelled state.
    assert run.state == "cancelled", f"run.state after cancel: {run.state!r}"

    # ---- Assertion 4: boundary fired within the POC budget.
    assert boundary_seen_at, "drainer did not observe a RunCancelledEvent"
    elapsed = boundary_seen_at[0] - started_at
    assert elapsed <= _BOUNDARY_BUDGET_SECONDS, (
        f"cancel boundary took {elapsed:.3f}s > {_BOUNDARY_BUDGET_SECONDS}s budget"
    )

    # Sanity: cancel does NOT emit a terminal ResultEvent (cancelled is
    # not in :class:`ResultEvent.status` Literal -- see loop.py 277-288).
    result_events = [ev for ev in received if isinstance(ev, ResultEvent)]
    assert not result_events, (
        f"expected no terminal ResultEvent on cancel; got "
        f"{[type(e).__name__ for e in result_events]!r}"
    )
