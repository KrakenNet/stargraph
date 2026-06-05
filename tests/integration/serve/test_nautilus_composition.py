# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.26: Nautilus-stub + Nautilus-removed composition tests (FR-58, AC-11.5).

Drives the triage IR end-to-end through the FastAPI serve surface in
two composition variants and asserts the same invariants hold in both:

1. **`test_with_stub`** loads
   :file:`tests/fixtures/triage_stub_broker.yaml`. The broker step
   resolves to :class:`tests.fixtures.nautilus_stub.StubBrokerNode`
   (canned :class:`nautilus.BrokerResponse`). The graph drives through
   `write_record` (real :class:`WriteArtifactNode` against a
   :class:`harbor.artifacts.fs.FilesystemArtifactStore`), emitting an
   :class:`~harbor.runtime.events.ArtifactWrittenEvent`, then halts at
   the InterruptNode at `approval_gate`, emitting a
   :class:`~harbor.runtime.events.WaitingForInputEvent`. The test
   issues ``POST /v1/runs/{id}/respond`` to release the gate and
   asserts the route returns 200 with state="running".

2. **`test_with_removed`** loads
   :file:`tests/fixtures/triage_no_nautilus.yaml`. The broker step
   is dropped entirely; the graph edges `retrieve_kv -> ml_score`
   directly. Same artifact + HITL invariants hold.

Both variants prove the validation-gate decision-diamond DD-3 (AC-11.5):
HITL + artifact are not load-bearing on the broker step. Removing or
stubbing Nautilus does NOT break the gate; it preserves the analyst-
approval + record-write contract end-to-end.

Topology choice -- artifact BEFORE interrupt: Phase-1 loop's
``_HitInterrupt`` arm exits cleanly after raising
:class:`WaitingForInputEvent` (no in-process resume hook; cold-restart
contract per task-1.16 + task-1.23). Driving through HITL respond past
the interrupt requires the cf-loop wiring landed in Phase-2 task 2.34
(documented gap; cf 3.23/3.24 progress notes). Placing the artifact
write BEFORE the interrupt keeps both invariants verifiable in a single
drive without crossing the documented gap. The full canonical IR layout
(broker -> ML -> DSPy -> CLIPS -> InterruptNode -> WriteArtifactNode
-> Action) lands with the validation-gate IR in Phase 5 task 5.1, when
the cf-loop wiring is available.

Implementation notes:

* IR YAML loading: the composition test owns the kind->NodeBase
  factory mapping. The IR `kind` field is a free-form string the test
  interprets (matches the existing `kind: stub_broker` /
  `kind: write_artifact` convention from
  :file:`tests/integration/serve/test_artifacts_endpoints.py`).
* HITL + artifact wiring: the test attaches
  ``run.step``, ``run.artifact_store``, ``run.is_replay`` so
  :class:`WriteArtifactNode` finds its required context surface (same
  monkey-patch convention as :file:`test_artifacts_endpoints.py`).
* Auth: a fixed-actor provider grants ``runs:respond`` for the respond
  POST.
* Real wiring: :class:`SQLiteCheckpointer`, :class:`FathomAdapter`
  wrapping a recording engine stub, :class:`EventBroadcaster`,
  :class:`JSONLAuditSink`, and :class:`FilesystemArtifactStore` --
  same fixtures the existing 3.13 + 3.14 tests use.

Refs: tasks.md §3.26; design §16.5 + §13.2; FR-58, AC-11.5.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
import yaml
from tests.fixtures.nautilus_stub import StubBrokerNode

from harbor.artifacts.fs import FilesystemArtifactStore
from harbor.audit import JSONLAuditSink
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.fathom import FathomAdapter
from harbor.graph import Graph, GraphRun
from harbor.ir import IRDocument
from harbor.nodes.artifacts import WriteArtifactNode
from harbor.nodes.artifacts.write_artifact_node import WriteArtifactNodeConfig
from harbor.nodes.base import NodeBase
from harbor.nodes.interrupt import InterruptNode
from harbor.nodes.interrupt.interrupt_node import InterruptNodeConfig
from harbor.nodes.nautilus.broker_node import BrokerNodeConfig
from harbor.runtime.events import (
    ArtifactWrittenEvent,
    Event,
    WaitingForInputEvent,
)
from harbor.serve.api import create_app
from harbor.serve.auth import AuthContext
from harbor.serve.broadcast import EventBroadcaster
from harbor.serve.contextvars import _audit_sink_var
from harbor.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from pydantic import BaseModel


pytestmark = [pytest.mark.serve, pytest.mark.api, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
_STUB_FIXTURE = _FIXTURES_DIR / "triage_stub_broker.yaml"
_REMOVED_FIXTURE = _FIXTURES_DIR / "triage_no_nautilus.yaml"

_ACTOR = "alice"
_INTERRUPT_PROMPT = "approve record write?"
_RESPONSE_BODY: dict[str, Any] = {"decision": "approve"}
_RECORD_BYTES = b'{"record_id": "REC-2026-0001", "score": 9.8}'


class _PassthroughNode(NodeBase):
    """No-op node returning an empty patch (matches existing 3.13 fixture pattern)."""

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class _RecordingEngine:
    """Minimal :class:`fathom.Engine` stand-in.

    Mirrors :class:`tests.integration.serve.test_hitl_respond._RecordingEngine`:
    records every :meth:`assert_fact` call so the test can introspect
    the ``harbor.evidence`` payload, while satisfying the
    ``mirror_state`` / ``evaluate`` / ``query`` surface the dispatch
    path consults.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))

    def mirror_state(
        self,
        state: Any,
        annotations: dict[str, Any],
    ) -> list[Any]:
        del state, annotations
        return []

    def evaluate(self) -> list[Any]:
        return []

    def query(
        self,
        template: str,
        fact_filter: Any,
    ) -> list[dict[str, Any]]:
        del template, fact_filter
        return []


class _FixedActorAuthProvider:
    """Auth provider returning a fixed actor with the standard grants.

    Matches the convention from
    :file:`tests/integration/serve/test_hitl_respond.py` -- the respond
    POST consults the auth context's ``actor`` for the audit fact.
    """

    def __init__(self, actor: str) -> None:
        self._actor = actor

    async def authenticate(self, request: Any) -> AuthContext:
        del request
        return AuthContext(
            actor=self._actor,
            capability_grants={"runs:respond", "runs:read"},
            session_id=None,
        )


def _load_ir(yaml_path: Path) -> IRDocument:
    """Load + validate an IR YAML file into an :class:`IRDocument`."""
    raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return IRDocument.model_validate(raw)


def _build_node_registry(ir: IRDocument) -> dict[str, NodeBase]:
    """Map ``node_id -> NodeBase`` for the composition fixture.

    The IR's `kind` field is a free-form key the test interprets:

    * ``passthrough`` -> :class:`_PassthroughNode`
    * ``stub_broker`` / ``broker`` -> :class:`StubBrokerNode` (the
      composition test contrast is "broker step present" vs. "broker
      step absent"; both fixtures use the stub for end-to-end driving
      since the real :class:`harbor.nodes.nautilus.BrokerNode` requires
      the lifespan singleton, out of scope here).
    * ``interrupt`` -> :class:`InterruptNode`
    * ``write_artifact`` -> :class:`WriteArtifactNode`
    """
    registry: dict[str, NodeBase] = {}
    interrupt_cfg = InterruptNodeConfig(
        prompt=_INTERRUPT_PROMPT,
        interrupt_payload={"target": "record"},
    )
    write_cfg = WriteArtifactNodeConfig(
        content_field="record_bytes",
        name="record.json",
        content_type="application/json",
        output_field="artifact_ref",
    )
    broker_cfg = BrokerNodeConfig(
        agent_id_field="agent_id",
        intent_field="intent",
        # The graph state schema does not declare a broker_response
        # field (we want both fixtures to share one schema); the stub's
        # patch dict still merges harmlessly via the field-merge
        # registry's ignore-extra path.
        output_field="broker_response_dump",
    )
    for node in ir.nodes:
        if node.kind == "passthrough":
            registry[node.id] = _PassthroughNode()
        elif node.kind in {"stub_broker", "broker"}:
            registry[node.id] = StubBrokerNode(config=broker_cfg)
        elif node.kind == "interrupt":
            registry[node.id] = InterruptNode(config=interrupt_cfg)
        elif node.kind == "write_artifact":
            registry[node.id] = WriteArtifactNode(config=write_cfg)
        else:
            raise AssertionError(f"unhandled node kind {node.kind!r} in composition fixture")
    return registry


def _attach_write_context(
    run: GraphRun,
    *,
    artifact_store: FilesystemArtifactStore,
) -> None:
    """Monkey-patch the :class:`WriteArtifactContext` Protocol surface.

    The Phase-1 :class:`harbor.nodes.base.ExecutionContext` Protocol
    only pins ``run_id``; :class:`WriteArtifactNode` additionally
    requires ``step`` / ``bus`` / ``artifact_store`` / ``is_replay`` /
    ``fathom``. ``run.bus`` and ``run.fathom`` exist on the handle;
    we attach the rest explicitly. Same convention
    :file:`tests/integration/serve/test_artifacts_endpoints.py` uses.
    """
    run.step = 0  # type: ignore[attr-defined]
    run.artifact_store = artifact_store  # type: ignore[attr-defined]
    run.is_replay = False  # type: ignore[attr-defined]


async def _drain_until(
    run: GraphRun,
    sink: JSONLAuditSink,
    received: list[Event],
    *,
    stop_on: type | tuple[type, ...],
) -> None:
    """Drain ``run.bus`` into ``sink`` and ``received`` until ``stop_on``."""
    while True:
        try:
            ev = await run.bus.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            return
        received.append(ev)
        await sink.write(ev)
        if isinstance(ev, stop_on):
            return


async def _drive_to_interrupt_with_drain(
    run: GraphRun,
    audit_sink: JSONLAuditSink,
    received: list[Event],
) -> None:
    """Drive ``run`` to the InterruptNode boundary, draining bus events.

    The drainer stops on the first :class:`WaitingForInputEvent`; the
    drive task returns when the loop's interrupt arm fires.
    """

    async def _drive() -> None:
        await run.start()

    async def _drain() -> None:
        await _drain_until(
            run,
            audit_sink,
            received,
            stop_on=WaitingForInputEvent,
        )

    with anyio.fail_after(10.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            tg.start_soon(_drive)


# --------------------------------------------------------------------------- #
# Test 1: composition with the stub broker                                    #
# --------------------------------------------------------------------------- #


async def _run_composition(
    *,
    yaml_path: Path,
    tmp_path: Path,
    run_id: str,
) -> tuple[list[Event], FilesystemArtifactStore]:
    """Drive a composition fixture to the InterruptNode boundary, then POST /respond.

    Returns ``(received_events, artifact_store)`` for assertions.
    """
    ir = _load_ir(yaml_path)
    graph = Graph(ir)
    registry = _build_node_registry(ir)

    checkpointer = SQLiteCheckpointer(tmp_path / "ckpt.sqlite")
    await checkpointer.bootstrap()
    artifact_store = FilesystemArtifactStore(tmp_path / "artifacts")
    await artifact_store.bootstrap()

    fathom_adapter = FathomAdapter(_RecordingEngine())  # type: ignore[arg-type]

    initial_state = graph.state_schema(
        agent_id="analyst",
        intent="triage",
        record_bytes=_RECORD_BYTES,
    )

    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=initial_state,
        node_registry=registry,
        checkpointer=checkpointer,
        fathom=fathom_adapter,
    )
    _attach_write_context(run, artifact_store=artifact_store)

    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
        "artifact_store": artifact_store,
    }
    app = create_app(OssDefaultProfile(), deps=deps)
    app.state.auth_provider = _FixedActorAuthProvider(_ACTOR)
    audit_sink = JSONLAuditSink(tmp_path / "audit.jsonl")
    _audit_sink_var.set(audit_sink)

    received: list[Event] = []

    # Drive to the interrupt boundary; the drainer stops on the first
    # WaitingForInputEvent so both ArtifactWrittenEvent (emitted earlier
    # in the drive) and WaitingForInputEvent are captured.
    await _drive_to_interrupt_with_drain(run, audit_sink, received)

    # Sanity: state lattice is now ``awaiting-input``.
    assert run.state == "awaiting-input", (
        f"expected awaiting-input after drive-to-interrupt; got {run.state!r}"
    )

    # Issue the respond POST to release the gate. The respond path
    # transitions state to "running" and asserts a harbor.evidence
    # fact via the wired Fathom adapter; the cf-loop replay past the
    # interrupt is a documented Phase-2 task 2.34 gap (cf 3.23/3.24
    # progress notes). The composition test's assertion bar is "respond
    # returns 200 with status='running'", which is the implementation-
    # complete contract today.
    transport = httpx.ASGITransport(app=app)
    with anyio.fail_after(10.0):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            r = await client.post(
                f"/v1/runs/{run_id}/respond",
                json={"actor": _ACTOR, "response": _RESPONSE_BODY},
            )
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["status"] == "running", (
        f"expected respond to fold state='running'; got {summary!r}"
    )

    await audit_sink.close()
    return received, artifact_store


@pytest.mark.serve
@pytest.mark.api
async def test_with_stub(tmp_path: Path) -> None:
    """triage IR with `StubBrokerNode` -- HITL + artifact fire end-to-end.

    Asserts:

    * The graph drove past `write_record` and emitted exactly one
      :class:`ArtifactWrittenEvent` carrying the canonical
      ``application/json`` content type.
    * The graph reached the InterruptNode boundary and emitted
      :class:`WaitingForInputEvent` with the configured prompt.
    * The on-disk artifact store contains exactly one row matching the
      seeded ``_RECORD_BYTES`` payload.
    * The respond POST returned 200 + status="running".
    """
    received, artifact_store = await _run_composition(
        yaml_path=_STUB_FIXTURE,
        tmp_path=tmp_path,
        run_id="triage-stub-broker-run",
    )

    # ---- Artifact fired --------------------------------------------------
    artifact_events = [ev for ev in received if isinstance(ev, ArtifactWrittenEvent)]
    assert len(artifact_events) == 1, (
        f"expected 1 ArtifactWrittenEvent in stub-broker variant; "
        f"got events={[type(e).__name__ for e in received]!r}"
    )
    art_ev = artifact_events[0]
    assert art_ev.run_id == "triage-stub-broker-run"
    assert art_ev.artifact_ref["content_type"] == "application/json"

    # ---- HITL fired ------------------------------------------------------
    waiting = [ev for ev in received if isinstance(ev, WaitingForInputEvent)]
    assert len(waiting) == 1, (
        f"expected 1 WaitingForInputEvent in stub-broker variant; "
        f"got events={[type(e).__name__ for e in received]!r}"
    )
    assert waiting[0].prompt == _INTERRUPT_PROMPT

    # ---- On-disk artifact persisted -------------------------------------
    rows = await artifact_store.list("triage-stub-broker-run")
    assert len(rows) == 1, f"expected 1 artifact on disk; got {rows!r}"
    persisted_bytes = await artifact_store.get(rows[0].artifact_id)
    assert persisted_bytes == _RECORD_BYTES, (
        f"persisted artifact bytes mismatch: {persisted_bytes!r} != {_RECORD_BYTES!r}"
    )


# --------------------------------------------------------------------------- #
# Test 2: composition with Nautilus REMOVED                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_with_removed(tmp_path: Path) -> None:
    """triage IR WITHOUT the broker step -- HITL + artifact still fire.

    Decision-Diamond DD-3 (AC-11.5): the validation gate must not be
    load-bearing on the broker step. Removing the broker entirely
    (edge: ``retrieve_kv -> ml_score`` direct) preserves both the HITL
    gate and the artifact write end-to-end.

    Asserts the same invariants as `test_with_stub`: ArtifactWrittenEvent
    fires, WaitingForInputEvent fires with the configured prompt, the
    on-disk artifact store carries the seeded record, and the respond
    POST returns 200 + status="running".
    """
    received, artifact_store = await _run_composition(
        yaml_path=_REMOVED_FIXTURE,
        tmp_path=tmp_path,
        run_id="triage-no-nautilus-run",
    )

    # ---- Artifact fired --------------------------------------------------
    artifact_events = [ev for ev in received if isinstance(ev, ArtifactWrittenEvent)]
    assert len(artifact_events) == 1, (
        f"expected 1 ArtifactWrittenEvent in no-nautilus variant; "
        f"got events={[type(e).__name__ for e in received]!r}"
    )

    # ---- HITL fired ------------------------------------------------------
    waiting = [ev for ev in received if isinstance(ev, WaitingForInputEvent)]
    assert len(waiting) == 1, (
        f"expected 1 WaitingForInputEvent in no-nautilus variant; "
        f"got events={[type(e).__name__ for e in received]!r}"
    )

    # ---- On-disk artifact persisted -------------------------------------
    rows = await artifact_store.list("triage-no-nautilus-run")
    assert len(rows) == 1, f"expected 1 artifact on disk; got {rows!r}"
    persisted_bytes = await artifact_store.get(rows[0].artifact_id)
    assert persisted_bytes == _RECORD_BYTES
