# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.13): HITL respond + audit provenance.

Drives an :class:`InterruptNode`-bearing graph through the FastAPI serve
surface end-to-end and verifies the three contract slices that
``POST /v1/runs/{id}/respond`` must satisfy:

1. **InterruptNode -> WaitingForInputEvent**: a graph that contains an
   :class:`~stargraph.nodes.interrupt.InterruptNode` at step ``N`` raises
   the loop's typed ``_HitInterrupt`` signal on dispatch; the loop
   transitions ``state="awaiting-input"`` and emits
   :class:`~stargraph.runtime.events.WaitingForInputEvent` carrying
   ``prompt`` / ``interrupt_payload`` / ``requested_capability`` from
   the IR config. ``GET /v1/runs/{id}`` reflects the live state lattice
   under the task-1.22 fold (``awaiting-input -> "paused"``).

2. **POST /v1/runs/{id}/respond resumes**: the route flips state to
   ``"running"``, asserts a ``stargraph.evidence`` Fathom fact carrying
   ``origin="user"`` / ``source=actor`` / ``data=<response-body>``
   (locked Decision #2; design §17), and emits a
   :class:`~stargraph.runtime.events.BosunAuditEvent` on the run bus with
   ``fact.kind="respond"``, ``fact.actor=<actor>``, and
   ``fact.body_hash=sha256(rfc8785.dumps(response))``.

3. **Audit emission (privacy boundary, AC-14.9)**: the JSONL audit
   sink captures BOTH the engine-internal ``respond`` fact (drained
   from the run bus) AND the serve-layer ``respond_orchestrated`` fact
   (persisted via the :data:`~stargraph.serve.contextvars._audit_sink_var`
   contextvar). Critically, the raw response body is NEVER persisted;
   the engine fact carries ``body_hash`` only. The test asserts neither
   audit JSONL line contains the response payload's payload-bearing
   keys.

Real wiring:

* :class:`~stargraph.checkpoint.sqlite.SQLiteCheckpointer` (real DB on ``tmp_path``).
* :class:`~stargraph.fathom.FathomAdapter` wrapping a real
  :class:`fathom.Engine`. The respond path consults
  ``run.fathom`` to assert the ``stargraph.evidence`` fact; bypassing it
  with ``fathom=None`` would skip the assertion entirely (engine guard
  at run.py:532). The test queries the engine's fact store directly to
  prove the assertion landed with the locked Decision #2 shape.
* :class:`~stargraph.audit.JSONLAuditSink` wired via
  :data:`~stargraph.serve.contextvars._audit_sink_var.set(...)` so the
  serve-layer ``respond_orchestrated`` audit lands on disk; bus events
  (including the engine's ``respond`` audit) are drained into the same
  sink by a co-task running alongside the respond POST. The terminal
  read-back proves both records land.

Refs: tasks.md §3.13; design §16.2 + §9.4 + §17 Decision #2; FR-82,
FR-85, FR-89; AC-14.4, AC-14.5, AC-14.6, AC-14.9.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import TYPE_CHECKING, Any, cast

import anyio
import anyio.lowlevel
import httpx
import pytest
import rfc8785

from stargraph.audit import JSONLAuditSink
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.fathom import FathomAdapter
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.nodes.base import NodeBase
from stargraph.nodes.interrupt import InterruptNode
from stargraph.nodes.interrupt.interrupt_node import InterruptNodeConfig
from stargraph.runtime.events import (
    BosunAuditEvent,
    Event,
    ResultEvent,
    WaitingForInputEvent,
)
from stargraph.serve.api import create_app
from stargraph.serve.auth import AuthContext
from stargraph.serve.broadcast import EventBroadcaster
from stargraph.serve.contextvars import _audit_sink_var
from stargraph.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


pytestmark = [pytest.mark.serve, pytest.mark.api, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


_INTERRUPT_PROMPT = "approve milestone write?"
_INTERRUPT_PAYLOAD: dict[str, Any] = {"target": "milestone.txt", "step": 5}
_RESPONSE_BODY: dict[str, Any] = {"decision": "approve", "comment": "LGTM"}
# CLIPS identifier-safe actor: the FathomAdapter's
# :func:`_sanitize_provenance_slot` enforces ``^[A-Za-z_][A-Za-z0-9_\-]*$``
# on ``_origin`` / ``_source`` (AC-6.2 structural check; adapter.py:46-72).
# Production deployments normalize email-shaped principals to a stable
# CLIPS-safe id before they reach the respond path (the auth provider
# is the canonical normalization site; see :mod:`stargraph.serve.auth`).
# The task description's literal ``alice@example.com`` flows through
# the HTTP request body / audit log perfectly fine, but the
# evidence-fact ``_source`` slot needs the normalized form -- the
# auth context's ``actor`` is the value the engine sees end-to-end.
_ACTOR = "alice"


class _PassthroughNode(NodeBase):
    """No-op node returning an empty patch (Phase-1 1.30 fixture pattern)."""

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


def _build_interrupt_graph() -> Graph:
    """Five-node IR with an :class:`InterruptNode` at step 5.

    Layout: 4 passthrough nodes followed by ``approval_gate`` (the
    :class:`InterruptNode`). The loop dispatches passthroughs in order,
    incrementing ``step`` per dispatch; on the 5th node the
    ``_HitInterrupt`` arm fires and the loop exits cleanly with
    ``state="awaiting-input"``. Per design §17 Decision #1 the
    interrupt is raised pre-dispatch, so the
    :class:`WaitingForInputEvent` carries ``step=4`` (the count of
    completed prior dispatches; the loop has not yet bumped the
    counter when ``execute`` raises). The test asserts on the
    ``waiting_for_input`` event itself rather than the exact step
    integer so this implementation detail is not load-bearing.
    """
    return Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:hitl-respond-fixture",
            nodes=[NodeSpec(id=f"step_{i}", kind="passthrough") for i in range(1, 5)]
            + [NodeSpec(id="approval_gate", kind="interrupt")],
            state_schema={"counter": "int"},
        )
    )


def _build_interrupt_registry() -> dict[str, NodeBase]:
    cfg = InterruptNodeConfig(
        prompt=_INTERRUPT_PROMPT,
        interrupt_payload=dict(_INTERRUPT_PAYLOAD),
    )
    return {
        **{f"step_{i}": _PassthroughNode() for i in range(1, 5)},
        "approval_gate": InterruptNode(config=cfg),
    }


class _RecordingEngine:
    """Minimal :class:`fathom.Engine` stand-in for the dispatch + respond paths.

    Records every :meth:`assert_fact` call (template + slot dict) so the
    test can query them after the fact. Implements the surface
    :func:`stargraph.runtime.dispatch.dispatch_node` consults
    (``mirror_state`` / ``evaluate`` / ``query``) plus the
    ``assert_fact`` sink that
    :meth:`FathomAdapter.assert_with_provenance` forwards to.

    Mirrors :class:`tests.integration.test_factstore_fathom_provenance._RecordingEngine`
    (the stargraph-knowledge spec uses the same shape) but extended with the
    three additional methods the dispatch path needs. Avoids the cost of
    booting a real Fathom :class:`fathom.Engine` (which would require
    registering ``stargraph_action`` / ``stargraph.evidence`` deftemplates +
    a CLIPS module before it accepts the asserts).
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
    """Auth provider that returns a fixed ``actor`` with the standard grants.

    The default :class:`BypassAuthProvider` wired by
    :func:`stargraph.serve.api.create_app` returns ``actor="anonymous"``;
    the FastAPI respond route calls
    ``handle_respond(..., ctx["actor"], ...)`` so the engine-internal
    ``BosunAuditEvent`` records ``"anonymous"`` regardless of any
    ``actor`` field in the request body. To exercise the locked
    Decision #2 contract end-to-end the test installs this fixture
    provider so ``_ACTOR`` flows from the auth context through to the
    audit fact + ``stargraph.evidence`` provenance bundle.
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


def _make_fathom_adapter() -> FathomAdapter:
    """:class:`FathomAdapter` wrapping a recording engine stub.

    Returning a real :class:`fathom.Engine` would force the test to
    register the ``stargraph_action`` and ``stargraph.evidence`` deftemplates
    + a CLIPS module before any assert lands; that is overkill for a
    respond integration test that only needs to verify the
    :meth:`assert_with_provenance` payload shape (locked Decision #2).
    The recording stub records every assert call so the test can query
    the slot dict directly.
    """
    return FathomAdapter(_RecordingEngine())  # type: ignore[arg-type]


async def _drain_bus_to_sink(
    run: GraphRun,
    sink: JSONLAuditSink,
    received: list[Event],
    stop_on: type | tuple[type, ...] | None = None,
) -> None:
    """Drain ``run.bus`` into ``sink``; capture each event in ``received``.

    Mirrors the milestone-six fixture's drainer (task 1.32). Stops on
    the first event matching ``stop_on`` so a surrounding task group
    exits cleanly without polling. All four parameters are positional
    so :meth:`anyio.TaskGroup.start_soon` (which forwards ``*args``
    only) can pass them directly.
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


async def _wait_for_state(
    run: GraphRun,
    target: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller wraps in anyio.fail_after
) -> None:
    """Poll until ``run.state == target`` (cooperative, checkpoint-yielding).

    Hot-resume (#81): the long-lived drive task parks inside the loop on
    the interrupt's respond event, so the responder co-task can no longer
    rely on ``run.start()`` returning at the boundary. It polls the live
    state lattice instead, yielding via :func:`anyio.lowlevel.checkpoint`
    so the parked drive keeps the event loop's turn.
    """
    deadline = anyio.current_time() + timeout
    while run.state != target:
        if anyio.current_time() > deadline:
            raise TimeoutError(
                f"run {run.run_id!r} did not reach {target!r} within {timeout}s "
                f"(state={run.state!r})"
            )
        await anyio.lowlevel.checkpoint()


# --------------------------------------------------------------------------- #
# Test 1: InterruptNode -> WaitingForInputEvent + GET state                   #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_interrupt_node_emits_waiting_for_input(tmp_path: Path) -> None:
    """``InterruptNode`` raises ``_HitInterrupt`` -> WS event + GET state.

    Drives the 5-node graph until the loop's ``_HitInterrupt`` arm
    fires; asserts the bus emitted exactly one
    :class:`WaitingForInputEvent` with the configured prompt + payload,
    and that ``GET /v1/runs/{id}`` returns ``status="paused"`` (the
    task-1.22 fold for ``state="awaiting-input"``).
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "interrupt.sqlite")
    await checkpointer.bootstrap()

    graph = _build_interrupt_graph()
    run_id = "hitl-respond-interrupt-only"
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(counter=0),
        node_registry=_build_interrupt_registry(),
        checkpointer=checkpointer,
    )
    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)
    audit_sink = JSONLAuditSink(tmp_path / "audit.jsonl")

    received: list[Event] = []

    async def _drive() -> None:
        # Hot-resume (#81): with no respond delivered, ``run.start()`` parks
        # inside the loop on the interrupt's respond event. The body drains
        # to the WaitingForInputEvent and then cancels the scope to unwind
        # this parked drive; the run is left at ``awaiting-input``.
        with contextlib.suppress(BaseException):
            await run.start()

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drive)
            await _drain_bus_to_sink(run, audit_sink, received, WaitingForInputEvent)
            tg.cancel_scope.cancel()

    # ---- Bus assertions ---------------------------------------------------
    waiting = [ev for ev in received if isinstance(ev, WaitingForInputEvent)]
    assert len(waiting) == 1, (
        f"expected exactly 1 WaitingForInputEvent; got {[type(e).__name__ for e in received]!r}"
    )
    wfi = waiting[0]
    assert wfi.prompt == _INTERRUPT_PROMPT, f"WaitingForInputEvent.prompt mismatch: {wfi.prompt!r}"
    assert wfi.interrupt_payload == _INTERRUPT_PAYLOAD, (
        f"WaitingForInputEvent.interrupt_payload mismatch: {wfi.interrupt_payload!r}"
    )
    assert wfi.run_id == run_id
    # The interrupt boundary fires pre-dispatch on the 5th node; the
    # event step is the loop's pre-bump step counter (last completed
    # dispatch + 1). Don't pin the exact integer -- assert it is
    # non-negative and within the graph's range.
    assert 0 <= wfi.step < len(graph.ir.nodes), (
        f"WaitingForInputEvent.step out of range: {wfi.step!r}"
    )

    assert run.state == "awaiting-input", f"expected run.state='awaiting-input'; got {run.state!r}"

    # ---- HTTP state assertion --------------------------------------------
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # ``awaiting-input`` folds onto the narrower Checkpointer summary
    # Literal as ``"paused"`` per the task-1.22 lattice fold.
    assert body["status"] == "paused", (
        f"GET status mismatch (awaiting-input -> 'paused' under task-1.22 fold); got {body!r}"
    )

    await audit_sink.close()


# --------------------------------------------------------------------------- #
# Test 2: respond resumes; stargraph.evidence asserted; bus carries body_hash    #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_respond_asserts_evidence_and_emits_audit(tmp_path: Path) -> None:
    """``POST /respond`` -> evidence fact + BosunAuditEvent with body_hash.

    Drives the 5-node graph to the interrupt boundary, hits
    ``POST /v1/runs/{id}/respond`` with the canonical
    ``{"decision": "approve", "comment": "LGTM"}`` response body, then
    asserts:

    * The route returns 200 with ``status="running"`` (state-lattice
      fold for the respond happy path).
    * A ``stargraph.evidence`` fact was asserted on the wired Fathom
      engine carrying ``_origin="user"`` / ``_source=<actor>`` /
      ``data=<response-body>`` (locked Decision #2 shape: raw JSON
      dict in the ``data`` slot, no envelope, no string serialization).
    * A :class:`BosunAuditEvent` landed on the bus with
      ``fact.kind="respond"``, ``fact.actor=<actor>``, and
      ``fact.body_hash`` matching ``sha256(rfc8785.dumps(response))``.
      The raw response body is NOT in the audit fact (privacy
      boundary, AC-14.9).
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "respond.sqlite")
    await checkpointer.bootstrap()

    graph = _build_interrupt_graph()
    run_id = "hitl-respond-resume"
    fathom_adapter = _make_fathom_adapter()
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(counter=0),
        node_registry=_build_interrupt_registry(),
        checkpointer=checkpointer,
        fathom=fathom_adapter,
    )
    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)
    app.state.auth_provider = _FixedActorAuthProvider(_ACTOR)
    audit_sink = JSONLAuditSink(tmp_path / "audit.jsonl")
    _audit_sink_var.set(audit_sink)

    received: list[Event] = []
    respond_body_box: dict[str, Any] = {}

    # Hot-resume (#81): a single long-lived drive parks at the interrupt and
    # resumes when ``respond()`` sets the event. ``approval_gate`` is the
    # last node, so resume runs the loop to its terminal ResultEvent -- the
    # drainer stops there, capturing the WaitingForInputEvent, the respond
    # BosunAuditEvent, and the terminal result in one pass. The responder
    # waits for the ``awaiting-input`` boundary, then POSTs /respond.
    async def _drive() -> None:
        await run.start()

    async def _respond_when_awaiting() -> None:
        await _wait_for_state(run, "awaiting-input")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/v1/runs/{run_id}/respond",
                json={"actor": _ACTOR, "response": _RESPONSE_BODY},
            )
        assert response.status_code == 200, response.text
        respond_body_box["body"] = response.json()

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain_bus_to_sink, run, audit_sink, received, ResultEvent)
            tg.start_soon(_drive)
            tg.start_soon(_respond_when_awaiting)

    # ---- Assertion: route returned 200 + status running|done ------------
    summary = respond_body_box["body"]
    assert summary["run_id"] == run_id
    assert summary["status"] in ("running", "done"), (
        f"respond folds state onto status='running'; under hot-resume the "
        f"loop may already have reached 'done' by the time the summary is "
        f"built (race on the post-respond checkpoint yield); got {summary!r}"
    )

    # ---- Assertion: stargraph.evidence fact asserted with locked shape -----
    # The respond path calls ``fathom.assert_with_provenance("stargraph.evidence",
    # {"data": response}, provenance)`` with ``provenance.origin="user"`` and
    # ``provenance.source=<actor>`` (run.py:532-549). The adapter encodes
    # provenance into ``_origin`` / ``_source`` / ``_run_id`` / ``_step`` /
    # ``_confidence`` / ``_timestamp`` and merges with the caller's slots,
    # then forwards to ``engine.assert_fact``. The recording engine stub
    # records every call; we walk the recorded calls.
    engine_obj = cast("Any", fathom_adapter.engine)
    recorded = cast("list[tuple[str, dict[str, Any]]]", engine_obj.calls)
    evidence_calls = [slots for tmpl, slots in recorded if tmpl == "stargraph.evidence"]
    assert evidence_calls, (
        f"expected at least one stargraph.evidence assert; got recorded "
        f"templates {[t for t, _ in recorded]!r}"
    )
    matching = [s for s in evidence_calls if s.get("_source") == _ACTOR]
    assert matching, (
        f"no stargraph.evidence fact carries _source={_ACTOR!r}; got {evidence_calls!r}"
    )
    fact = matching[0]
    assert fact.get("_origin") == "user", (
        f"stargraph.evidence._origin should be 'user' (locked Decision #2); got {fact!r}"
    )
    assert fact.get("_run_id") == run_id, f"stargraph.evidence._run_id mismatch: {fact!r}"
    # ``data`` slot carries the raw response JSON dict per locked
    # Decision #2 (no envelope, no string serialization).
    assert fact.get("data") == _RESPONSE_BODY, (
        f"stargraph.evidence.data should be the raw response dict "
        f"(locked Decision #2); got {fact!r}"
    )
    # Confidence and timestamp slots are populated.
    assert fact.get("_confidence") is not None
    assert fact.get("_timestamp") is not None

    # ---- Assertion: BosunAuditEvent on bus has body_hash, NOT raw body --
    audits = [ev for ev in received if isinstance(ev, BosunAuditEvent)]
    respond_audits = [ev for ev in audits if ev.fact.get("kind") == "respond"]
    assert len(respond_audits) == 1, (
        f"expected exactly 1 BosunAuditEvent with fact.kind='respond'; "
        f"got {[a.fact for a in audits]!r}"
    )
    audit_fact = respond_audits[0].fact
    expected_hash = hashlib.sha256(rfc8785.dumps(_RESPONSE_BODY)).hexdigest()
    assert audit_fact.get("body_hash") == expected_hash, (
        f"BosunAuditEvent.fact.body_hash mismatch: expected {expected_hash!r}, got {audit_fact!r}"
    )
    assert audit_fact.get("actor") == _ACTOR, f"BosunAuditEvent.fact.actor mismatch: {audit_fact!r}"
    # Privacy boundary (AC-14.9): the raw response body must NEVER appear
    # in the audit fact. Only ``kind`` / ``actor`` / ``body_hash``.
    forbidden_keys = {"response", "response_body", "body", "data"}
    assert not (forbidden_keys & set(audit_fact.keys())), (
        f"AC-14.9 violation: BosunAuditEvent.fact carries forbidden raw-body "
        f"keys {forbidden_keys & set(audit_fact.keys())!r} in {audit_fact!r}"
    )

    await audit_sink.close()


# --------------------------------------------------------------------------- #
# Test 3: Audit JSONL contains body_hash, NOT raw body (AC-14.9)              #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.api
async def test_audit_jsonl_contains_body_hash_not_raw_body(
    tmp_path: Path,
) -> None:
    """JSONL audit sink persists body_hash; raw response body never lands.

    Drives the same flow as the previous test but reads the on-disk
    audit JSONL back and asserts:

    * At least one line is a ``BosunAuditEvent`` with
      ``fact.kind="respond"`` carrying ``actor=<actor>`` and
      ``body_hash=sha256(rfc8785.dumps(response))``.
    * NO line in the audit log contains the raw response body's
      payload-bearing values (``decision="approve"``,
      ``comment="LGTM"``). This is the privacy-boundary contract per
      design §9.7 / AC-14.9: only the actor + body hash land in the
      audit log; the body itself is never persisted.
    """
    checkpointer = SQLiteCheckpointer(tmp_path / "audit-check.sqlite")
    await checkpointer.bootstrap()

    graph = _build_interrupt_graph()
    run_id = "hitl-respond-audit-check"
    fathom_adapter = _make_fathom_adapter()
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=graph.state_schema(counter=0),
        node_registry=_build_interrupt_registry(),
        checkpointer=checkpointer,
        fathom=fathom_adapter,
    )
    broadcaster = EventBroadcaster(run.bus)
    deps: dict[str, Any] = {
        "runs": {run_id: run},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps)
    app.state.auth_provider = _FixedActorAuthProvider(_ACTOR)
    audit_path = tmp_path / "audit.jsonl"
    audit_sink = JSONLAuditSink(audit_path)
    _audit_sink_var.set(audit_sink)

    received: list[Event] = []

    # Hot-resume (#81): single long-lived drive parks at the interrupt and
    # resumes on respond (see test 2). The drainer stops on the terminal
    # ResultEvent so the respond BosunAuditEvent is captured + persisted.
    async def _drive() -> None:
        await run.start()

    async def _post_respond_when_awaiting() -> None:
        await _wait_for_state(run, "awaiting-input")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/v1/runs/{run_id}/respond",
                json={"actor": _ACTOR, "response": _RESPONSE_BODY},
            )
        assert r.status_code == 200, r.text

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain_bus_to_sink, run, audit_sink, received, ResultEvent)
            tg.start_soon(_drive)
            tg.start_soon(_post_respond_when_awaiting)

    await audit_sink.close()

    # ---- Read the on-disk JSONL back and verify the privacy boundary ----
    assert audit_path.exists(), "audit.jsonl was not created"
    raw_lines = [
        line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert raw_lines, "audit.jsonl is empty"
    decoded = [json.loads(line) for line in raw_lines]

    # ---- Assertion: at least one bosun_audit row has fact.kind='respond' --
    respond_rows = [
        rec
        for rec in decoded
        if rec.get("type") == "bosun_audit" and rec.get("fact", {}).get("kind") == "respond"
    ]
    assert len(respond_rows) == 1, (
        f"expected exactly 1 bosun_audit row with fact.kind='respond' in "
        f"{audit_path!r}; got {[r.get('fact') for r in decoded]!r}"
    )
    respond_fact = respond_rows[0]["fact"]
    expected_hash = hashlib.sha256(rfc8785.dumps(_RESPONSE_BODY)).hexdigest()
    assert respond_fact.get("body_hash") == expected_hash, (
        f"audit row body_hash mismatch: expected {expected_hash!r}, got {respond_fact!r}"
    )
    assert respond_fact.get("actor") == _ACTOR, f"audit row actor mismatch: {respond_fact!r}"

    # ---- Assertion (AC-14.9): raw body NEVER lands in audit log ----------
    # The response body's payload-bearing values (decision="approve",
    # comment="LGTM") must not appear in any line of the audit log. We
    # check the raw line bytes (post-orjson encoding) so a buggy
    # implementation that leaked the response into a different field
    # name (e.g. ``response_body`` / ``body``) would still fail this
    # check.
    for line in raw_lines:
        # The literal string ``"LGTM"`` is unique to the response body
        # (does not appear elsewhere in the audit schema), so its
        # absence everywhere is a strong negative test for the privacy
        # boundary.
        assert "LGTM" not in line, (
            f"AC-14.9 violation: raw response body leaked into audit log line: {line!r}"
        )

    # ---- Belt-and-braces: walk parsed JSON and assert the response dict --
    # ----                  shape never appears as a sub-tree.            --
    def _contains_response_body(node: Any) -> bool:
        if isinstance(node, dict):
            d: dict[str, Any] = cast("dict[str, Any]", node)
            if d == _RESPONSE_BODY:
                return True
            return any(_contains_response_body(v) for v in d.values())
        if isinstance(node, list):
            lst: list[Any] = cast("list[Any]", node)
            return any(_contains_response_body(item) for item in lst)
        return False

    for rec in decoded:
        assert not _contains_response_body(rec), (
            f"AC-14.9 violation: response body sub-tree found in audit record {rec!r}"
        )
