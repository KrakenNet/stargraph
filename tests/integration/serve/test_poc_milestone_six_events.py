# SPDX-License-Identifier: Apache-2.0
"""POC MILESTONE -- all 6 new event variants flow end-to-end (task 1.32).

This is the **Phase 1 POC gate**. Failure here halts Phase 2 entry per
``tasks.md`` line 473. The test proves five integration claims simultaneously:

1. Engine TODO closed -- ``GraphRun.cancel/pause/respond`` produce real
   typed events on the bus.
2. IR ``InterruptAction`` dispatches per **Resolved Decision #1** -- the
   loop's ``_HitInterrupt`` arm raises a ``WaitingForInputEvent`` and
   transitions to ``state="awaiting-input"``.
3. All 6 new typed :data:`Event` variants flow through
   :class:`~stargraph.audit.JSONLAuditSink` transparently. The sink uses
   ``pydantic.TypeAdapter[Event]`` dispatch (design §3.12, §4.3) so no
   per-variant code is needed -- the union-encoded line carries the
   ``type`` Literal and a JSONL replay reader recovers the variant.
4. The FastAPI HTTP/WS surface accepts ``POST /respond``,
   ``POST /pause``, ``POST /cancel`` and emits the matching events (the
   broadcaster fan-out is the WS surface; the test consumes ``run.bus``
   directly because the broadcaster is not driven here).
5. :class:`~stargraph.nodes.artifacts.WriteArtifactNode` writes
   BLAKE3-addressed content and emits a typed
   :class:`~stargraph.runtime.events.ArtifactWrittenEvent`.

The 6 variants, mapped to test invocations:

* :class:`WaitingForInputEvent` -- Run A (interrupt boundary).
* :class:`ArtifactWrittenEvent` -- Run A (writer node, post-respond resume).
* :class:`BosunAuditEvent` -- Run A (``GraphRun.respond()`` step 2;
  commit a680374, task 1.7).
* :class:`RunPausedEvent` -- Run B (long-running stub graph + HTTP pause;
  fixture mirrors task 1.31).
* :class:`RunCancelledEvent` -- Run C (long-running stub graph + HTTP
  cancel; fixture mirrors task 1.31).
* :class:`InterruptTimeoutEvent` -- Run D, **synthesized** directly into
  the audit sink. Engine-level enforcement now lands in the loop
  (:func:`stargraph.graph.loop._await_respond_or_timeout` emits the event
  when the watchdog wins; #81), and its timer behaviour is covered by
  :mod:`tests.integration.test_interrupt_timeout`. This aggregate
  milestone asserts serialization, not timer precision, so Run D keeps
  the direct synthesis to stay timing-free; the variant's
  ``TypeAdapter[Event]`` round-trip is identical either way.

The single :class:`JSONLAuditSink` ingests every event from every run.
The terminal assertion reads the JSONL back, decodes each line, and
verifies all 6 ``type`` Literals appear at least once.

Refs: tasks.md §1.32 (POC milestone callout, line 473); design §4.3
(event union), §17 (locked decisions); FR-76, FR-79, FR-83, FR-87,
FR-93, FR-38; AC-13.4, AC-14.3, AC-15.4.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anyio
import anyio.lowlevel
import httpx
import pytest

from stargraph.artifacts.fs import FilesystemArtifactStore
from stargraph.audit import JSONLAuditSink
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.nodes.artifacts import WriteArtifactNode
from stargraph.nodes.artifacts.write_artifact_node import WriteArtifactNodeConfig
from stargraph.nodes.base import NodeBase
from stargraph.nodes.interrupt import InterruptNode
from stargraph.nodes.interrupt.interrupt_node import InterruptNodeConfig
from stargraph.runtime.events import (
    ArtifactWrittenEvent,
    BosunAuditEvent,
    Event,
    InterruptTimeoutEvent,
    ResultEvent,
    RunCancelledEvent,
    RunPausedEvent,
    WaitingForInputEvent,
)
from stargraph.serve.api import create_app
from stargraph.serve.broadcast import EventBroadcaster
from stargraph.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


pytestmark = pytest.mark.serve


# --------------------------------------------------------------------------- #
# Fixture nodes                                                               #
# --------------------------------------------------------------------------- #


class _PassthroughNode(NodeBase):
    """No-op node returning an empty patch (mirrors task 1.30 fixture)."""

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class _LongRunningStubNode(NodeBase):
    """Cooperative-yield stub (mirrors task 1.31 fixture).

    Each ``execute`` awaits :func:`anyio.sleep` for ``tick_seconds`` so
    the loop stretches over a wider time window, letting concurrent HTTP
    requests drive lifecycle methods between ticks.
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
# Constants                                                                   #
# --------------------------------------------------------------------------- #


_NUM_LONG_NODES = 12
_TICK_SECONDS = 0.05
_BOUNDARY_BUDGET_SECONDS = 2.0


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_three_node_graph() -> Graph:
    """3-node IR: ``approval_gate`` -> ``passthrough`` -> ``writer``."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:poc-milestone-runA",
        nodes=[
            NodeSpec(id="approval_gate", kind="interrupt"),
            NodeSpec(id="passthrough", kind="passthrough"),
            NodeSpec(id="writer", kind="write_artifact"),
        ],
        state_schema={"content_to_write": "bytes"},
    )
    return Graph(ir)


def _build_three_node_registry() -> dict[str, NodeBase]:
    interrupt_cfg = InterruptNodeConfig(
        prompt="approve write?",
        interrupt_payload={"target": "milestone.txt"},
    )
    writer_cfg = WriteArtifactNodeConfig(
        content_field="content_to_write",
        name="milestone.txt",
        content_type="text/plain",
    )
    return {
        "approval_gate": InterruptNode(config=interrupt_cfg),
        "passthrough": _PassthroughNode(),
        "writer": WriteArtifactNode(config=writer_cfg),
    }


def _build_long_running_graph(run_id: str) -> Graph:
    """12-node stub graph for cancel/pause exercises."""
    nodes = [NodeSpec(id=f"step_{i}", kind="stub") for i in range(_NUM_LONG_NODES)]
    ir = IRDocument(
        ir_version="1.0.0",
        id=run_id,
        nodes=nodes,
        state_schema={"counter": "int"},
    )
    return Graph(ir)


def _build_long_running_registry() -> dict[str, NodeBase]:
    return {
        f"step_{i}": _LongRunningStubNode(tick_seconds=_TICK_SECONDS)
        for i in range(_NUM_LONG_NODES)
    }


def _attach_write_context(
    run: GraphRun,
    *,
    artifact_store: FilesystemArtifactStore,
) -> None:
    """Monkey-patch the :class:`WriteArtifactContext` Protocol surface (task 1.30 pattern)."""
    run.step = 0  # type: ignore[attr-defined]
    run.artifact_store = artifact_store  # type: ignore[attr-defined]
    run.is_replay = False  # type: ignore[attr-defined]


async def _wait_for_running(
    run: GraphRun,
    *,
    timeout: float = 1.0,  # noqa: ASYNC109 -- anyio.fail_after wraps the call site
) -> None:
    """Poll until ``run.state == 'running'`` (mirrors task 1.31 helper)."""
    deadline = anyio.current_time() + timeout
    while run.state != "running":
        if anyio.current_time() > deadline:
            raise TimeoutError(
                f"run {run.run_id!r} did not reach 'running' within {timeout}s "
                f"(state={run.state!r})"
            )
        await anyio.lowlevel.checkpoint()


async def _wait_for_state(
    run: GraphRun,
    target: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 -- anyio.fail_after wraps the call site
) -> None:
    """Poll until ``run.state == target`` (hot-resume responder helper, #81).

    The long-lived drive parks inside the loop on the interrupt's respond
    event, so the responder co-task polls the live state lattice instead of
    waiting for ``run.start()`` to return at the boundary.
    """
    deadline = anyio.current_time() + timeout
    while run.state != target:
        if anyio.current_time() > deadline:
            raise TimeoutError(
                f"run {run.run_id!r} did not reach {target!r} within {timeout}s "
                f"(state={run.state!r})"
            )
        await anyio.lowlevel.checkpoint()


async def _drain_to_sink(
    run: GraphRun,
    sink: JSONLAuditSink,
    received: list[Event],
    stop_on: type | tuple[type, ...] | None = None,
) -> None:
    """Drain ``run.bus`` into ``sink``; capture each event in ``received``.

    If ``stop_on`` is supplied, the drainer returns on the first matching
    event so the surrounding task group can exit. Bus closure (anyio
    EndOfStream / ClosedResourceError) ends the drain cleanly.
    All four parameters are positional so :meth:`anyio.TaskGroup.start_soon`
    (which forwards ``*args`` only) can pass them directly.
    """
    while True:
        try:
            ev = await run.bus.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            return
        received.append(ev)
        await sink.write(ev)
        if stop_on is not None and isinstance(ev, stop_on):
            return


# --------------------------------------------------------------------------- #
# Run A: interrupt -> respond -> write artifact                                #
# --------------------------------------------------------------------------- #


async def _run_a_interrupt_respond_write(
    *,
    tmp_path: Path,
    sink: JSONLAuditSink,
) -> dict[str, Any]:
    """Run A -- emits ``WaitingForInputEvent`` + ``BosunAuditEvent`` + ``ArtifactWrittenEvent``.

    Hot-resume (#81): drive the 3-node graph (approval_gate -> passthrough
    -> writer) on a single live coroutine. ``run.start()`` parks at the
    interrupt; ``POST /respond`` (emitting a :class:`BosunAuditEvent` from
    :meth:`GraphRun.respond` step 2) wakes the same coroutine, which
    advances through ``passthrough`` and ``writer`` (emitting the
    :class:`ArtifactWrittenEvent`) to a terminal :class:`ResultEvent`. All
    three Run-A variants land in one end-to-end drive -- no synthetic tail
    run, because the loop now resumes past the interrupt in-process.
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "runA.sqlite")
    await checkpointer.bootstrap()
    artifact_store = FilesystemArtifactStore(tmp_path / "runA-artifacts")
    await artifact_store.bootstrap()

    # --- Phase 1: drive to the interrupt boundary --------------------------
    graph = _build_three_node_graph()
    run_id = "poc-milestone-runA"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(content_to_write=b"hello milestone"),
        node_registry=_build_three_node_registry(),
        checkpointer=checkpointer,
    )
    _attach_write_context(run, artifact_store=artifact_store)

    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    received: list[Event] = []

    # Hot-resume (#81): the 3-node graph (approval_gate -> passthrough ->
    # writer) runs on a single live coroutine. ``run.start()`` parks at the
    # interrupt; the responder POSTs /respond once the run reaches
    # ``awaiting-input``; the loop then advances through ``passthrough`` and
    # ``writer`` (emitting ArtifactWrittenEvent) to a terminal ResultEvent.
    # One drive yields all three Run-A variants in a single pass:
    # WaitingForInputEvent, BosunAuditEvent (respond), ArtifactWrittenEvent.
    async def _drive() -> None:
        await run.start()

    async def _respond_when_awaiting() -> None:
        await _wait_for_state(run, "awaiting-input")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/v1/runs/{run_id}/respond",
                json={"response": {"approved": True}},
            )
        assert response.status_code == 200, (
            f"Run A respond: {response.status_code} body={response.text!r}"
        )

    with anyio.fail_after(10.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain_to_sink, run, sink, received, ResultEvent)
            tg.start_soon(_drive)
            tg.start_soon(_respond_when_awaiting)

    assert any(isinstance(ev, WaitingForInputEvent) for ev in received), (
        f"Run A: expected WaitingForInputEvent in stream; got "
        f"{[type(e).__name__ for e in received]!r}"
    )
    assert any(isinstance(ev, BosunAuditEvent) for ev in received), (
        f"Run A: expected BosunAuditEvent post-respond; got "
        f"{[type(e).__name__ for e in received]!r}"
    )
    assert any(isinstance(ev, ArtifactWrittenEvent) for ev in received), (
        f"Run A: expected ArtifactWrittenEvent after hot-resume past the "
        f"interrupt; got {[type(e).__name__ for e in received]!r}"
    )

    return {"received": received}


# --------------------------------------------------------------------------- #
# Run B: pause                                                                 #
# --------------------------------------------------------------------------- #


async def _run_b_pause(
    *,
    tmp_path: Path,
    sink: JSONLAuditSink,
) -> None:
    """Run B -- emits ``RunPausedEvent`` (mirrors task 1.31 pause fixture)."""
    checkpointer = SQLiteCheckpointer(tmp_path / "runB.sqlite")
    await checkpointer.bootstrap()

    graph = _build_long_running_graph("run:poc-milestone-runB")
    run_id = "poc-milestone-runB"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(counter=0),
        node_registry=_build_long_running_registry(),
        checkpointer=checkpointer,
    )
    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    received: list[Event] = []

    async def _drive() -> None:
        await run.start()

    async def _issue_pause() -> None:
        await _wait_for_running(run)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/v1/runs/{run_id}/pause")
        assert response.status_code == 200, (
            f"Run B pause: {response.status_code} body={response.text!r}"
        )

    with anyio.fail_after(_BOUNDARY_BUDGET_SECONDS + 1.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                _drain_to_sink,
                run,
                sink,
                received,
                # Stop on terminal ResultEvent (pause boundary emits one).
                ResultEvent,
            )
            tg.start_soon(_drive)
            tg.start_soon(_issue_pause)

    assert any(isinstance(ev, RunPausedEvent) for ev in received), (
        f"Run B: expected RunPausedEvent; got {[type(e).__name__ for e in received]!r}"
    )


# --------------------------------------------------------------------------- #
# Run C: cancel                                                                #
# --------------------------------------------------------------------------- #


async def _run_c_cancel(
    *,
    tmp_path: Path,
    sink: JSONLAuditSink,
) -> None:
    """Run C -- emits ``RunCancelledEvent`` (mirrors task 1.31 cancel fixture)."""
    checkpointer = SQLiteCheckpointer(tmp_path / "runC.sqlite")
    await checkpointer.bootstrap()

    graph = _build_long_running_graph("run:poc-milestone-runC")
    run_id = "poc-milestone-runC"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(counter=0),
        node_registry=_build_long_running_registry(),
        checkpointer=checkpointer,
    )
    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    received: list[Event] = []

    async def _drive() -> None:
        # Cancel raises CancelledError out of run.start(); suppress so the
        # task group's siblings can complete (per task 1.31 pattern).
        try:
            await run.start()
        except asyncio.CancelledError:
            return

    async def _issue_cancel() -> None:
        await _wait_for_running(run)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/v1/runs/{run_id}/cancel")
        assert response.status_code == 200, (
            f"Run C cancel: {response.status_code} body={response.text!r}"
        )

    with anyio.fail_after(_BOUNDARY_BUDGET_SECONDS + 1.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                _drain_to_sink,
                run,
                sink,
                received,
                # Stop on first RunCancelledEvent (cancel does NOT emit a
                # terminal ResultEvent -- task 1.31 learning).
                RunCancelledEvent,
            )
            tg.start_soon(_drive)
            tg.start_soon(_issue_cancel)

    assert any(isinstance(ev, RunCancelledEvent) for ev in received), (
        f"Run C: expected RunCancelledEvent; got {[type(e).__name__ for e in received]!r}"
    )


# --------------------------------------------------------------------------- #
# Run D: synthesize InterruptTimeoutEvent                                      #
# --------------------------------------------------------------------------- #


async def _run_d_synthesize_interrupt_timeout(
    *,
    sink: JSONLAuditSink,
) -> None:
    """Run D -- synthesize :class:`InterruptTimeoutEvent` directly into the sink.

    Engine-level interrupt-timeout enforcement now lands in the loop:
    :func:`stargraph.graph.loop._await_respond_or_timeout` races the
    ``InterruptAction.timeout`` watchdog against the respond event and
    emits :class:`InterruptTimeoutEvent` when the timer wins (#81). Its
    timer behaviour (±100ms precision, ``halt`` / ``goto`` policy) is
    covered by :mod:`tests.integration.test_interrupt_timeout`.

    This aggregate milestone asserts "all 6 variants are producible and
    serializable end-to-end through the engine + audit sink" -- not timer
    precision -- so Run D keeps the direct synthesis to stay timing-free.
    A directly constructed :class:`InterruptTimeoutEvent` written through
    :meth:`JSONLAuditSink.write` proves the ``TypeAdapter[Event]`` dispatch
    handles the variant identically to the loop-emitted ones (same
    Pydantic union, same JSON shape).
    """
    ev = InterruptTimeoutEvent(
        run_id="poc-milestone-runD-synth",
        step=0,
        ts=datetime.now(UTC),
        on_timeout="halt",
    )
    await sink.write(ev)


# --------------------------------------------------------------------------- #
# Test                                                                        #
# --------------------------------------------------------------------------- #


_REQUIRED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run_paused",
        "run_cancelled",
        "waiting_for_input",
        "interrupt_timeout",
        "artifact_written",
        "bosun_audit",
    }
)


@pytest.mark.serve
@pytest.mark.integration
async def test_poc_milestone_six_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """POC MILESTONE: all 6 new event variants land in the JSONL audit sink.

    This is the **Phase 1 POC gate** (tasks.md line 473). The test runs
    four invocations in sequence, all writing to a single shared
    :class:`JSONLAuditSink`:

    * Run A -- 3-node interrupt -> respond -> write artifact graph
      emits ``WaitingForInputEvent``, ``BosunAuditEvent``, and
      ``ArtifactWrittenEvent`` end-to-end via the engine + FastAPI
      ``POST /respond`` route. Mirrors task 1.30's fixture.
    * Run B -- 12-node long-running stub graph + ``POST /pause``
      emits ``RunPausedEvent``. Mirrors task 1.31's pause scenario.
    * Run C -- same fixture + ``POST /cancel`` emits
      ``RunCancelledEvent``. Mirrors task 1.31's cancel scenario.
    * Run D -- ``InterruptTimeoutEvent`` is **synthesized** directly to
      the sink because engine-level interrupt-timeout enforcement is
      deferred to Phase 2 (task 1.11's resume hook is not yet wired).
      The synthesis proves the ``TypeAdapter[Event]`` dispatch handles
      the variant identically to loop-emitted variants.

    Final assertion reads the JSONL log back, decodes each line via
    :func:`json.loads`, collects ``type`` Literals into a set, and
    confirms all 6 required types appear. The test prints
    ``POC_MILESTONE_PASS`` on success so the verify command's
    ``&& echo POC_MILESTONE_PASS`` chains reliably.
    """
    audit_log = tmp_path / "poc-milestone.audit.jsonl"
    sink = JSONLAuditSink(audit_log)
    try:
        await _run_a_interrupt_respond_write(tmp_path=tmp_path, sink=sink)
        await _run_b_pause(tmp_path=tmp_path, sink=sink)
        await _run_c_cancel(tmp_path=tmp_path, sink=sink)
        await _run_d_synthesize_interrupt_timeout(sink=sink)
    finally:
        await sink.close()

    # ---- Final assertion: all 6 type Literals appear in the JSONL log.
    types_seen: set[str] = set()
    line_count = 0
    for raw in audit_log.read_text().splitlines():
        if not raw.strip():
            continue
        line_count += 1
        record = json.loads(raw)
        # Unsigned mode (Phase 1 default) -- the line IS the event payload.
        # Signed mode would wrap as {"event": <payload>, "sig": "..."}; the
        # POC sink is constructed without a signing key.
        types_seen.add(record["type"])

    missing = _REQUIRED_EVENT_TYPES - types_seen
    assert not missing, (
        f"POC MILESTONE FAIL: missing event types {sorted(missing)!r}; "
        f"saw {sorted(types_seen)!r} across {line_count} JSONL records"
    )

    # The verify command chains `&& echo POC_MILESTONE_PASS`; the test
    # also prints the marker so the pytest -v output carries it.
    print("POC_MILESTONE_PASS")
    captured = capsys.readouterr()
    assert "POC_MILESTONE_PASS" in captured.out
