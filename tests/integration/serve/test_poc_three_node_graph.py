# SPDX-License-Identifier: Apache-2.0
"""POC integration smoke -- 3-node graph: Interrupt -> Respond -> WriteArtifact.

Drives a minimal 3-node IR through the FastAPI serve surface
(:func:`stargraph.serve.api.create_app`) end-to-end:

1. ``approval_gate`` -- :class:`stargraph.nodes.interrupt.InterruptNode` raises
   the loop's typed ``_HitInterrupt`` signal carrying an
   :class:`InterruptAction` payload. The loop transitions
   ``state="awaiting-input"`` and emits
   :class:`~stargraph.runtime.events.WaitingForInputEvent`.
2. ``passthrough`` -- a tiny inline :class:`NodeBase` that returns an empty
   patch. The 3-node graph's middle hop, exercising the post-respond resume
   path. (No FathomBranchNode-shaped passthrough exists in-tree yet.)
3. ``writer`` -- :class:`stargraph.nodes.artifacts.WriteArtifactNode` reads
   ``state.content_to_write`` (bytes), persists via the wired
   :class:`FilesystemArtifactStore`, and emits
   :class:`~stargraph.runtime.events.ArtifactWrittenEvent`.

Documented gaps (Phase 1 cold-restart contract; design §4.1 + loop.py
docstring):

* :meth:`GraphRun.respond` flips state from ``"awaiting-input"`` back to
  ``"running"``, but :func:`stargraph.graph.loop.execute` has already exited
  via the ``_HitInterrupt`` arm. Resume is the cold-restart path through
  :meth:`GraphRun.resume` (not yet wired through ``POST /respond``). For
  this POC test the post-respond resume is exercised by manually
  constructing a fresh :class:`GraphRun` over the ``[passthrough, writer]``
  tail of the graph -- this proves the WriteArtifactNode wiring (event
  emission + BLAKE3 content hash) without requiring the in-process
  resume hook.
* :class:`WriteArtifactNode` reads ``ctx.step`` / ``ctx.artifact_store`` /
  ``ctx.is_replay`` from its execution context; the Phase-1
  :class:`GraphRun` does not yet carry these (task 1.14 wiring gap). We
  monkey-patch them onto the run handle before driving so the
  ``runtime_checkable`` :class:`WriteArtifactContext` protocol matches.
* The serve API takes :class:`httpx.ASGITransport` (not the deprecated
  ``app=`` kwarg) per httpx >=0.27.

Refs: tasks.md §1.30; design §9 + §10 + §17 Decision #1; FR-82, FR-85,
FR-92, FR-93, AC-14.2, AC-14.4, AC-15.4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
from blake3 import blake3

from stargraph.artifacts.fs import FilesystemArtifactStore
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
    ResultEvent,
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
    """No-op middle hop returning an empty patch.

    Used as the second node of the 3-node POC fixture. No
    FathomBranchNode-shaped passthrough exists in-tree yet; this
    minimal stub exercises the dispatch + merge path between the
    interrupt boundary and the artifact write.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_three_node_graph() -> Graph:
    """Build the 3-node IR + compiled :class:`Graph` for the POC fixture.

    State schema carries the artifact payload as ``bytes``; the IR
    type-name policy (:data:`stargraph.graph.definition._TYPE_MAP`) supports
    ``str/int/bool/bytes`` -- ``bytes`` is the natural fit for an
    artifact's pre-write payload.
    """
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:poc-three-node",
        nodes=[
            NodeSpec(id="approval_gate", kind="interrupt"),
            NodeSpec(id="passthrough", kind="passthrough"),
            NodeSpec(id="writer", kind="write_artifact"),
        ],
        state_schema={"content_to_write": "bytes"},
    )
    return Graph(ir)


def _build_node_registry() -> dict[str, NodeBase]:
    """Wire the InterruptNode + passthrough + WriteArtifactNode trio."""
    interrupt_cfg = InterruptNodeConfig(
        prompt="approve write?",
        interrupt_payload={"target": "test.txt"},
    )
    writer_cfg = WriteArtifactNodeConfig(
        content_field="content_to_write",
        name="test.txt",
        content_type="text/plain",
    )
    return {
        "approval_gate": InterruptNode(config=interrupt_cfg),
        "passthrough": _PassthroughNode(),
        "writer": WriteArtifactNode(config=writer_cfg),
    }


def _attach_write_context(
    run: GraphRun,
    *,
    artifact_store: FilesystemArtifactStore,
) -> None:
    """Monkey-patch the :class:`WriteArtifactContext` Protocol surface onto ``run``.

    :class:`WriteArtifactNode` reads ``step`` / ``artifact_store`` /
    ``is_replay`` from its execution context (the run handle dispatch
    passes through). The Phase-1 :class:`GraphRun` does not carry these
    (task 1.14 wiring gap); we attach them ad-hoc so the
    ``runtime_checkable`` Protocol's ``isinstance`` check succeeds.
    Phase 2 promotes the fields onto :class:`GraphRun` proper.
    """
    # ``step`` is read at write-time as the node's checkpoint step;
    # the loop owns the true step counter, but the node ctx surface
    # has not been wired through ``dispatch_node`` yet (task 1.14
    # gap). ``0`` is a safe sentinel for the POC -- the
    # ``ArtifactWrittenEvent.step`` matches whatever we attach.
    run.step = 0  # type: ignore[attr-defined]
    run.artifact_store = artifact_store  # type: ignore[attr-defined]
    run.is_replay = False  # type: ignore[attr-defined]


async def _drive_run_until(
    run: GraphRun,
    *,
    stop_on: type | tuple[type, ...],
    timeout: float = 5.0,  # noqa: ASYNC109 -- anyio.fail_after used internally
) -> list[Any]:
    """Drive ``run.start()`` and drain ``run.bus`` until an event of ``stop_on``.

    The bus is single-consumer; the drainer is the sole receiver. Two
    concurrent tasks under one ``anyio`` task group:

    * ``_drive`` -- ``await run.start()``; the loop publishes events to
      the bus during dispatch.
    * ``_drain`` -- receives events one at a time and stops the task
      group via :class:`anyio.CancelScope` when an event of type
      ``stop_on`` arrives. The bus remains *open* on return so the
      caller can keep emitting (e.g. :meth:`GraphRun.respond` publishes
      a :class:`BosunAuditEvent` on the same bus).

    The interrupt arm of :func:`stargraph.graph.loop.execute` returns a
    :class:`RunSummary` and exits the loop (cold-restart contract);
    after that the drive task is done but the bus stays open for the
    serve-layer respond audit emission.
    """
    received: list[Any] = []

    async def _drive() -> None:
        await run.start()

    async def _drain(scope: anyio.CancelScope) -> None:
        while True:
            try:
                ev = await run.bus.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return
            received.append(ev)
            if isinstance(ev, stop_on):
                # Cancel the task group so the drive task (already
                # finished or about to finish) is awaited cleanly.
                scope.cancel()
                return

    with anyio.fail_after(timeout):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain, tg.cancel_scope)
            tg.start_soon(_drive)

    return received


# --------------------------------------------------------------------------- #
# Test                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.integration
async def test_poc_three_node_graph_interrupt_respond_write(tmp_path: Path) -> None:
    """End-to-end POC smoke: interrupt -> respond -> write artifact.

    Asserts:

    1. After driving the run, the bus emits :class:`WaitingForInputEvent`
       carrying the configured prompt + payload.
    2. ``POST /v1/runs/{run_id}/respond`` returns 200 with a structured
       :class:`RunSummary` body whose ``status`` reflects the post-respond
       state.
    3. After the post-respond resume tail (manually driven over
       ``[passthrough, writer]`` because the in-process resume hook
       has not been wired through ``POST /respond``), the bus emits
       :class:`ArtifactWrittenEvent`.
    4. The artifact's ``content_hash`` matches BLAKE3 of ``"hello world"``.
    """
    # --- Phase 0: shared store + checkpointer setup -------------------------
    # Both phases of the run (pre-respond + post-respond resume tail) share
    # the same SQLite checkpointer and FilesystemArtifactStore so the
    # artifact id round-trip is observable end-to-end.
    checkpointer = SQLiteCheckpointer(tmp_path / "poc.sqlite")
    await checkpointer.bootstrap()
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    await artifact_store.bootstrap()

    # --- Phase 1: build graph + run; drive to interrupt boundary -----------
    graph = _build_three_node_graph()
    initial_state = graph.state_schema(content_to_write=b"hello world")
    run_id = "poc-three-node-run"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=initial_state,
        node_registry=_build_node_registry(),
        checkpointer=checkpointer,
    )
    _attach_write_context(run, artifact_store=artifact_store)

    # Build the FastAPI app + register the run on the dep container
    # exactly like the Phase-2 lifespan would. The
    # :class:`EventBroadcaster` registry is populated for parity with
    # the design surface (the WS route reads ``deps["broadcasters"]``);
    # the broadcaster itself is not driven here -- the test consumes
    # ``run.bus`` directly because the broadcaster fan-out is the WS
    # surface, not the orchestrator's drive path.
    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    # Drive the run + drain the bus until the interrupt boundary. The
    # interrupt-arm of :func:`stargraph.graph.loop.execute` transitions
    # ``run.state="awaiting-input"``, emits :class:`WaitingForInputEvent`,
    # and returns cleanly (cold-restart contract). The drainer cancels
    # the task group on first WaitingForInputEvent so the bus stays
    # open for the post-respond audit emission.
    pre_respond_events = await _drive_run_until(run, stop_on=WaitingForInputEvent)

    # ---- Assertion 1: WaitingForInputEvent emitted with configured payload.
    waiting = [ev for ev in pre_respond_events if isinstance(ev, WaitingForInputEvent)]
    assert len(waiting) == 1, (
        f"expected exactly 1 WaitingForInputEvent, got {len(waiting)}; "
        f"events: {[type(e).__name__ for e in pre_respond_events]!r}"
    )
    assert waiting[0].prompt == "approve write?"
    assert waiting[0].interrupt_payload == {"target": "test.txt"}
    assert run.state == "awaiting-input"

    # --- Phase 2: POST /v1/runs/{run_id}/respond ---------------------------
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/v1/runs/{run_id}/respond",
            json={"response": {"approved": True}},
        )

    # ---- Assertion 2: respond returns 200 + structured summary.
    assert response.status_code == 200, (
        f"expected 200; got {response.status_code} body={response.text!r}"
    )
    body = response.json()
    assert "status" in body, f"respond body missing 'status': {body!r}"
    # :meth:`GraphRun.respond` flips state to ``"running"``; the serve
    # layer's :func:`_build_run_summary` maps that 1:1 onto
    # ``RunSummary.status``.
    assert body["status"] == "running", f"unexpected post-respond status: {body!r}"
    assert run.state == "running", f"run.state after respond: {run.state!r}"

    # --- Phase 3: manually drive the post-respond resume tail --------------
    # Documented gap: :func:`stargraph.graph.loop.execute` has already exited
    # via the ``_HitInterrupt`` arm above. Resume is cold-restart only --
    # the in-process resume hook for ``POST /respond`` is not wired
    # (loop.py docstring "Resume-from-respond hook"). For this POC we
    # construct a fresh :class:`GraphRun` over the ``[passthrough, writer]``
    # tail of the graph so the WriteArtifactNode wiring (event emission
    # + BLAKE3 content hash) is exercised end-to-end.
    tail_ir = IRDocument(
        ir_version="1.0.0",
        id="run:poc-three-node-tail",
        nodes=[
            NodeSpec(id="passthrough", kind="passthrough"),
            NodeSpec(id="writer", kind="write_artifact"),
        ],
        state_schema={"content_to_write": "bytes"},
    )
    tail_graph = Graph(tail_ir)
    tail_run_id = "poc-three-node-run-tail"
    tail_run = GraphRun(
        run_id=tail_run_id,
        graph=tail_graph,
        initial_state=tail_graph.state_schema(content_to_write=b"hello world"),
        node_registry={
            "passthrough": _PassthroughNode(),
            "writer": WriteArtifactNode(
                config=WriteArtifactNodeConfig(
                    content_field="content_to_write",
                    name="test.txt",
                    content_type="text/plain",
                ),
            ),
        },
        checkpointer=checkpointer,
    )
    _attach_write_context(tail_run, artifact_store=artifact_store)

    tail_events = await _drive_run_until(
        tail_run,
        stop_on=ResultEvent,
        timeout=5.0,
    )

    # ---- Assertion 3: ArtifactWrittenEvent emitted on the tail run.
    written = [ev for ev in tail_events if isinstance(ev, ArtifactWrittenEvent)]
    assert len(written) == 1, (
        f"expected exactly 1 ArtifactWrittenEvent, got {len(written)}; "
        f"events: {[type(e).__name__ for e in tail_events]!r}"
    )

    # ---- Assertion 4: artifact content_hash matches BLAKE3("hello world").
    expected_hash = blake3(b"hello world").hexdigest()
    artifact_ref = written[0].artifact_ref
    assert artifact_ref["content_hash"] == expected_hash, (
        f"content_hash mismatch: expected {expected_hash!r}, got {artifact_ref['content_hash']!r}"
    )

    # Sanity: the tail run reached terminal ``done`` (vs failing).
    assert tail_run.state == "done", f"tail run.state: {tail_run.state!r}"
    # And the bus saw a terminal :class:`ResultEvent` after the artifact
    # write -- confirms the loop drove past the ``writer`` node cleanly.
    result_events = [ev for ev in tail_events if isinstance(ev, ResultEvent)]
    assert len(result_events) == 1, (
        f"expected exactly 1 terminal ResultEvent, got {len(result_events)}"
    )
    # body_hash sanity (engine respond audit fact carries this; see
    # GraphRun.respond docstring step 2). The audit fact's body_hash is
    # sha256(rfc8785.dumps(response)) -- not asserted here directly
    # because the BosunAuditEvent is emitted on run.bus and Phase-1
    # audit-sink wiring is gap territory (task 2.30); we leave the deep
    # audit assertion to task 1.32.
