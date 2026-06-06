# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.9): full HTTP run lifecycle.

Drives the canonical run lifecycle through the FastAPI serve surface
(:func:`harbor.serve.api.create_app`) end-to-end:

1. ``POST /v1/runs`` returns ``202 Accepted`` with ``{run_id, status}``.
2. ``GET /v1/runs/{run_id}`` returns ``200`` with a structured
   :class:`RunSummary`-shaped body.
3. ``POST /v1/runs/{run_id}/cancel`` returns ``200`` with the updated
   summary; the run's ``state`` lattice transitions
   ``pending`` -> ``running`` -> ``cancelled``.
4. Each lifecycle transition emits a :class:`BosunAuditEvent`-shaped
   record into the wired :class:`JSONLAuditSink`. The test reads the
   JSONL audit file back and asserts the cancel-side audit fact lands.

Real wiring:

* :class:`SQLiteCheckpointer` (real DB on ``tmp_path``).
* :class:`EventBroadcaster` (in-memory broadcaster wrapping the run's
  bus) registered into ``deps["broadcasters"]`` for parity with the WS
  surface, even though this test does not subscribe via WS.
* :class:`Scheduler` is *not* required: the lifecycle test drives the
  HTTP routes directly against an in-process :class:`GraphRun` registered
  on ``app.state.deps["runs"]``. This matches the existing POC harness
  (commit 3677f30, task 1.30) and isolates the cancel/lifecycle path
  from the synthetic Scheduler stub. ``POST /v1/runs`` is exercised
  separately (asserts the route returns the canonical
  Scheduler-derived ``run_id``, not the legacy ``poc-{graph_id}``
  stub) but is not the run that the cancel route operates on.
* :class:`JSONLAuditSink` is wired via
  :data:`harbor.serve.contextvars._audit_sink_var.set(...)` so
  :func:`harbor.serve.lifecycle.cancel_run`'s audit-emit lands on disk
  and the test can read it back.

Refs: tasks.md §3.9; design §16.2 + §5.1; FR-12, FR-22, AC-7.1, AC-13.6.
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

from harbor.audit import JSONLAuditSink
from harbor.checkpoint.protocol import RunSummary
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.graph import Graph, GraphRun
from harbor.ir import IRDocument, NodeSpec
from harbor.nodes.base import NodeBase
from harbor.runtime.events import RunCancelledEvent, TransitionEvent
from harbor.serve.api import create_app
from harbor.serve.broadcast import EventBroadcaster
from harbor.serve.contextvars import _audit_sink_var
from harbor.serve.profiles import OssDefaultProfile
from harbor.serve.scheduler import EnqueueHandle, Scheduler

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pydantic import BaseModel


pytestmark = [pytest.mark.serve, pytest.mark.api, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Fixture nodes                                                               #
# --------------------------------------------------------------------------- #


_NUM_NODES = 12
_TICK_SECONDS = 0.05
_BOUNDARY_BUDGET_SECONDS = 2.0


class _LongRunningStubNode(NodeBase):
    """Cooperative-yield stub -- sleeps ``tick_seconds`` per execute.

    Mirrors the task 1.31 fixture: each tick yields cooperatively so
    concurrent HTTP requests can drive lifecycle methods between nodes.
    """

    def __init__(self, *, tick_seconds: float = _TICK_SECONDS) -> None:
        self.tick_seconds = tick_seconds

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        await anyio.sleep(self.tick_seconds)
        return {}


def _build_long_running_graph(graph_ir_id: str) -> Graph:
    """Build a 12-node IR + compiled :class:`Graph` for the lifecycle fixture."""
    nodes = [NodeSpec(id=f"step_{i}", kind="stub") for i in range(_NUM_NODES)]
    ir = IRDocument(
        ir_version="1.0.0",
        id=graph_ir_id,
        nodes=nodes,
        state_schema={"counter": "int"},
    )
    return Graph(ir)


def _build_long_running_registry() -> dict[str, NodeBase]:
    return {
        f"step_{i}": _LongRunningStubNode(tick_seconds=_TICK_SECONDS) for i in range(_NUM_NODES)
    }


class _StubScheduler:
    """Minimal Scheduler-shaped stub for the POST /v1/runs route.

    The real :class:`harbor.serve.scheduler.Scheduler` runs an
    in-process dispatcher + cron loop and requires async start/stop;
    this stub satisfies the FastAPI route handler's ``scheduler.enqueue``
    contract without spawning any background tasks. Returns a resolved
    Future immediately so no caller hangs.

    The lifecycle test asserts the route's ``202`` response shape and
    that the returned ``run_id`` matches the canonical Scheduler-derived
    id; we don't drive a real dispatcher here -- that's covered by
    ``tests/unit/serve/test_scheduler.py``.
    """

    def enqueue(
        self,
        graph_id: str,
        params: Mapping[str, Any],
        idempotency_key: str | None = None,
        *,
        trigger_source: str = "manual",
    ) -> EnqueueHandle:
        del params, trigger_source
        loop = asyncio.get_running_loop()
        future: asyncio.Future[RunSummary] = loop.create_future()
        now = datetime.now(UTC)
        # Mirror the real Scheduler's id derivation so the route's
        # response matches the contract callers rely on.
        key = idempotency_key or Scheduler._synth_idempotency_key(graph_id, now)  # pyright: ignore[reportPrivateUsage]
        run_id = Scheduler._derive_run_id(graph_id, key)  # pyright: ignore[reportPrivateUsage]
        future.set_result(
            RunSummary(
                run_id=run_id,
                graph_hash=graph_id,
                started_at=now,
                last_step_at=now,
                status="done",
                parent_run_id=None,
            )
        )
        return EnqueueHandle(run_id=run_id, future=future)


async def _wait_for_running(
    run: GraphRun,
    *,
    timeout: float = 1.0,  # noqa: ASYNC109 -- anyio.fail_after wraps the call site
) -> None:
    """Poll until ``run.state == 'running'`` (or raise :class:`TimeoutError`).

    Mirrors the task 1.31 helper -- the loop sets ``state="running"``
    just before the first :func:`dispatch_node` call. ``GraphRun.cancel``
    requires ``state in ('running', 'paused', 'awaiting-input')``.
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
# Test                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_full_run_lifecycle_post_get_cancel(tmp_path: Path) -> None:
    """End-to-end: POST /v1/runs (202) -> GET /v1/runs/{id} (200) -> cancel (200).

    Asserts each leg of the lifecycle:

    1. ``POST /v1/runs`` returns ``202 Accepted`` with ``{run_id, status:
       "pending"}``. The returned ``run_id`` is the canonical
       Scheduler-derived hash of (graph_id, idempotency_key); the
       legacy ``poc-{graph_id}`` stub is gone.
    2. ``GET /v1/runs/{run_id}`` returns ``200`` with a structured
       :class:`RunSummary` body for an in-memory-registered run; status
       reflects the live :attr:`GraphRun.state`.
    3. ``POST /v1/runs/{run_id}/cancel`` returns ``200`` with the
       post-cancel summary. After the cancel boundary fires, the run's
       lattice state is ``"cancelled"`` and the body's status is
       ``"failed"`` (task 1.22 mapping; Phase 2 widens the Literal).
    4. The bus emits exactly one :class:`RunCancelledEvent` and at least
       one :class:`TransitionEvent` (proving running->cancelled
       lattice transitions actually happened, not just a bus-side
       short-circuit).
    5. The wired :class:`JSONLAuditSink` captures a
       ``lifecycle_cancel`` :class:`BosunAuditEvent`-shaped fact.
    """
    # --- Phase 0: shared SQLite checkpointer + audit sink -------------------
    checkpointer = SQLiteCheckpointer(tmp_path / "lifecycle.sqlite")
    await checkpointer.bootstrap()
    audit_path = tmp_path / "audit.jsonl"
    audit_sink = JSONLAuditSink(audit_path)

    # --- Phase 1: POST /v1/runs via Scheduler stub -------------------------
    # ``_StubScheduler`` resolves its Future with a synthetic terminal
    # summary; we just need the route's 202 contract to hold without
    # spawning a real :class:`Scheduler` (whose stop() path interacts
    # poorly with the test harness's task-group teardown).
    scheduler = _StubScheduler()
    if True:
        # Build a separate live GraphRun for the cancel leg (the
        # Scheduler stub's synthetic run is not lattice-cancellable).
        graph = _build_long_running_graph("run:lifecycle-cancel-target")
        run_id = "lifecycle-cancel-target-run"
        run = GraphRun(
            run_id=run_id,
            graph=graph,
            initial_state=graph.state_schema(counter=0),
            node_registry=_build_long_running_registry(),
            checkpointer=checkpointer,
        )
        broadcaster = EventBroadcaster(run.bus)
        deps: dict[str, Any] = {
            "scheduler": scheduler,
            "runs": {run_id: run},
            "broadcasters": {run_id: broadcaster},
            "audit_path": audit_path,
        }
        app = create_app(OssDefaultProfile(), deps=deps)

        # Wire the audit sink via the contextvar so
        # :func:`harbor.serve.lifecycle.cancel_run` persists its
        # :class:`BosunAuditEvent` to disk. The contextvar is read by
        # the lifecycle helper directly via the run-handler's stack
        # frame; httpx ASGI transport preserves the surrounding context
        # since both run on the same asyncio event loop.
        _audit_sink_var.set(audit_sink)
        if True:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # ---- Step 1: POST /v1/runs -------------------------------
                start_resp = await client.post(
                    "/v1/runs",
                    json={"graph_id": "stub-graph", "params": {}},
                )
                assert start_resp.status_code == 202, (
                    f"expected 202 Accepted from POST /v1/runs; got "
                    f"{start_resp.status_code} body={start_resp.text!r}"
                )
                start_body = start_resp.json()
                assert "run_id" in start_body, f"start body missing run_id: {start_body!r}"
                assert start_body["status"] == "pending", (
                    f"unexpected start body status: {start_body!r}"
                )
                # Scheduler-derived run_id is a deterministic hash of
                # (graph_id, idempotency_key); just assert the shape
                # rather than a specific value (the synthesized
                # idempotency key embeds wall-clock time).
                assert isinstance(start_body["run_id"], str) and start_body["run_id"], (
                    f"missing/empty run_id in start body: {start_body!r}"
                )
                assert start_body["run_id"] != f"poc-{'stub-graph'}", (
                    "POST /v1/runs still returns the legacy poc-{graph_id} stub"
                )

                # ---- Step 2: GET /v1/runs/{run_id} -----------------------
                # Drive the in-memory cancellable run + observe the
                # GET-side state lattice + issue cancel concurrently.
                received: list[Any] = []
                cancel_resp_box: dict[str, httpx.Response] = {}

                async def _drive() -> None:
                    try:
                        await run.start()
                    except asyncio.CancelledError:
                        # Cooperative cancel propagates through the loop;
                        # suppress so the surrounding task group exits
                        # cleanly without aborting siblings.
                        return

                async def _drain() -> None:
                    while True:
                        try:
                            ev = await run.bus.receive()
                        except (anyio.EndOfStream, anyio.ClosedResourceError):
                            return
                        received.append(ev)
                        if isinstance(ev, RunCancelledEvent):
                            return

                async def _exercise_get_then_cancel() -> None:
                    # Wait for the loop to land on ``state="running"``
                    # so GET returns a non-pending status and cancel
                    # is lattice-valid.
                    await _wait_for_running(run)

                    # GET returns the live summary.
                    get_resp = await client.get(f"/v1/runs/{run_id}")
                    assert get_resp.status_code == 200, (
                        f"expected 200 from GET /v1/runs/{run_id}; got "
                        f"{get_resp.status_code} body={get_resp.text!r}"
                    )
                    get_body = get_resp.json()
                    assert get_body["run_id"] == run_id, f"GET body run_id mismatch: {get_body!r}"
                    # Status lattice fold (task 1.22 mapping):
                    # ``running`` is preserved 1:1.
                    assert get_body["status"] in ("running", "paused"), (
                        f"unexpected GET status: {get_body!r}"
                    )

                    # Give the loop one extra tick so at least one
                    # TransitionEvent is emitted before we issue cancel.
                    # The race is: the cancel route's RunCancelledEvent
                    # send lands on the bus before any node-tick
                    # TransitionEvent (since the first dispatch is still
                    # awaiting :func:`anyio.sleep`); waiting one tick
                    # keeps the assertion deterministic.
                    await anyio.sleep(_TICK_SECONDS * 2)

                    # POST /v1/runs/{run_id}/cancel
                    cancel_resp = await client.post(f"/v1/runs/{run_id}/cancel")
                    cancel_resp_box["resp"] = cancel_resp

                with anyio.fail_after(_BOUNDARY_BUDGET_SECONDS + 1.0):
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(_drain)
                        tg.start_soon(_drive)
                        tg.start_soon(_exercise_get_then_cancel)

                # ---- Step 3: validate cancel response --------------------
                cancel_resp = cancel_resp_box["resp"]
                assert cancel_resp.status_code == 200, (
                    f"expected 200 from cancel; got "
                    f"{cancel_resp.status_code} body={cancel_resp.text!r}"
                )
                cancel_body = cancel_resp.json()
                # ``cancelled`` folds onto the narrower
                # :class:`Checkpointer.RunSummary.status` Literal as
                # ``"failed"`` (task 1.22 mapping; Phase 2 widens).
                assert cancel_body["status"] == "failed", (
                    f"unexpected cancel body status: {cancel_body!r}"
                )

            # ---- Assertion 4: transition events emitted ---------------
            transitions = [ev for ev in received if isinstance(ev, TransitionEvent)]
            assert transitions, (
                "expected at least one TransitionEvent before cancel boundary; "
                "loop may have exited before observing the cancel signal"
            )
            cancelled = [ev for ev in received if isinstance(ev, RunCancelledEvent)]
            assert len(cancelled) == 1, (
                f"expected exactly 1 RunCancelledEvent, got {len(cancelled)}; "
                f"events: {[type(e).__name__ for e in received]!r}"
            )
            assert run.state == "cancelled", f"run.state after cancel: {run.state!r}"
    await audit_sink.close()

    # ---- Assertion 5: audit sink captured the lifecycle_cancel fact -------
    assert audit_path.exists(), "audit.jsonl was not created by the sink"
    audit_lines = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert audit_lines, (
        f"audit log is empty; expected at least one BosunAuditEvent line at {audit_path!r}"
    )
    cancel_audit = [
        rec
        for rec in audit_lines
        if rec.get("type") == "bosun_audit"
        and rec.get("fact", {}).get("kind") == "lifecycle_cancel"
        and rec.get("fact", {}).get("run_id") == run_id
    ]
    assert cancel_audit, (
        f"expected a lifecycle_cancel BosunAuditEvent for {run_id!r} in audit "
        f"log; got {audit_lines!r}"
    )
