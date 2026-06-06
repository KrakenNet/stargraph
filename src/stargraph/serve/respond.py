# SPDX-License-Identifier: Apache-2.0
"""HITL respond orchestration for the serve layer (POC slice).

This module owns the request-handler-side wrapper around
:meth:`stargraph.graph.GraphRun.respond`. The HTTP route in
:mod:`stargraph.serve.api` (task 1.24) calls into :func:`handle_respond`
after the FastAPI capability gate has accepted the request; the
orchestrator then performs the *engine-side* capability check
(default-deny in cleared profiles per NFR-7), verifies the run is in
``awaiting-input``, audit-emits a serve-layer
:class:`~stargraph.runtime.events.BosunAuditEvent`, and routes through
the live :class:`GraphRun` handle to deliver the response per design
§9.4.

POC scope (Phase 1, task 1.23):

* The "live :class:`GraphRun` handle lookup" is intentionally **stubbed**
  -- the in-process run registry that maps ``run_id -> GraphRun`` lands
  with the lifespan singleton wiring in Phase 2 (task 2.30). Until then,
  callers must pass the live ``run`` handle explicitly via the ``_run``
  keyword (used by tests and by the API factory once it threads the
  registry through). When ``_run`` is omitted, the orchestrator raises
  :class:`StargraphRuntimeError` with the same "registry not yet wired"
  message used by :func:`stargraph.serve.lifecycle.cancel_run` /
  :func:`pause_run` (task 1.22, commit 94119cb).
* The state-precondition check (``run.state == "awaiting-input"``) is
  enforced *before* delegating to :meth:`GraphRun.respond`. The engine
  method also enforces this invariant (run.py:510-513), so this is the
  outer guard that produces a serve-layer error matching the design
  §9.4 contract ("else 409 Conflict at the HTTP layer"); the inner
  guard is the engine's defense in depth.
* The audit emission is best-effort: if the audit sink contextvar
  (:data:`stargraph.serve.contextvars._audit_sink_var`) is unset (POC
  default) the :class:`BosunAuditEvent` is constructed but not
  persisted. The bus-side emission still flows through
  :meth:`GraphRun.respond` (which emits its own audit fact carrying
  ``actor`` + ``body_hash``), so the full audit trail captures both
  the API entry point (this module's serve-layer event) and the
  engine-internal respond fact.
* The capability check accepts an optional
  :class:`~stargraph.security.capabilities.Capabilities` argument. When
  ``None`` (POC default; cleared deployments will pin a non-``None``
  instance), no engine-side capability enforcement happens here -- the
  HTTP route gate is the only line of defense. Phase 2 wires the real
  capabilities object into the FastAPI lifespan and threads it through
  every orchestrator call.

Design refs: §9.4 Resume flow (5-step contract), §3.1 respond row
(``handle_respond(run_id, response, actor) -> RunSummary``), §4.1
state machine (respond is valid only from ``awaiting-input``), §9.7
(audit emission contract), §17 (lifecycle event emission contract).
FR-85, AC-14.4, AC-14.5, AC-14.6.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.runtime.events import BosunAuditEvent
from stargraph.serve.contextvars import _audit_sink_var  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from stargraph.checkpoint.protocol import RunSummary
    from stargraph.graph.run import GraphRun
    from stargraph.security.capabilities import Capabilities

__all__ = [
    "handle_respond",
]


# Audit pack identity. Mirrors the convention used by
# :mod:`stargraph.serve.lifecycle` (task 1.22): the serve-layer audit
# events flow under the runtime pack id so the audit replay can group
# them with the cancel/pause/respond surface emissions from
# :class:`GraphRun`. Phase 2 may rename to a dedicated
# ``stargraph.bosun.respond`` pack if the rule pack ships separately.
_RESPOND_PACK_ID = "stargraph.runtime"
_RESPOND_PACK_VERSION = "1.0"


def _check_capability(
    capabilities: Capabilities | None,
    required: str,
    actor: str,
) -> None:
    """Enforce the engine-side capability gate for the respond action.

    ``capabilities=None`` is the POC default and means "no engine-side
    enforcement; rely on the HTTP route gate". When a
    :class:`Capabilities` instance is wired (cleared deployments,
    Phase 2), the call to :meth:`Capabilities.has_permission` must
    return ``True`` for ``required`` (``"runs:respond"``) or this
    raises :class:`CapabilityError`.
    """
    if capabilities is None:
        return
    if not capabilities.has_permission(required):
        raise CapabilityError(
            f"actor {actor!r} lacks required capability {required!r}",
            actor=actor,
            required=required,
        )


def _build_audit_event(
    *,
    run_id: str,
    actor: str,
) -> BosunAuditEvent:
    """Construct the serve-layer :class:`BosunAuditEvent` for a respond call.

    ``step=0`` mirrors the convention in
    :meth:`GraphRun.cancel`/`pause`/`respond` and
    :func:`stargraph.serve.lifecycle._build_audit_event` for run-scoped
    (non-tick) emissions. The loop owns true step counters; lifecycle
    + respond surface events are not tied to a node boundary so the
    run-scoped sentinel is correct.

    The ``fact`` carries ``kind="respond_orchestrated"`` (distinct from
    the engine's ``kind="respond"`` fact emitted inside
    :meth:`GraphRun.respond`) so audit consumers can disambiguate the
    HTTP entry point from the engine-internal respond effect. The
    response body is *not* included here -- the engine fact carries
    ``body_hash=sha256(canonical_json(response))`` per design §9.7
    (compliance/privacy posture: never persist raw response bodies).
    """
    now = datetime.now(UTC)
    # ProvenanceBundle (FR-55, AC-11.2): the serve-layer respond audit
    # event is system-emitted (the HTTP entry point fires this
    # alongside, not in place of, the engine-internal respond fact);
    # origin="system" with the pack id as source matches the lifecycle
    # convention. The actor lives in the fact body for audit consumers.
    provenance: dict[str, Any] = {
        "origin": "system",
        "source": _RESPOND_PACK_ID,
        "run_id": run_id,
        "step": 0,
        "confidence": 1.0,
        "timestamp": now.isoformat(),
    }
    return BosunAuditEvent(
        run_id=run_id,
        step=0,
        ts=now,
        pack_id=_RESPOND_PACK_ID,
        pack_version=_RESPOND_PACK_VERSION,
        fact={"kind": "respond_orchestrated", "actor": actor, "run_id": run_id},
        provenance=provenance,
    )


async def _persist_audit(event: BosunAuditEvent) -> None:
    """Best-effort persist a serve-layer respond :class:`BosunAuditEvent`.

    Reads the audit sink from
    :data:`stargraph.serve.contextvars._audit_sink_var`. POC default is
    ``None`` (sink is wired by the Phase 2 lifespan factory in task
    2.30); when unwired the audit event is silently dropped at the
    persistence layer. The bus-side emission performed inside
    :meth:`GraphRun.respond` still publishes the engine-internal
    respond audit fact regardless of audit-sink wiring.
    """
    sink: Any | None = _audit_sink_var.get()
    if sink is None:
        return
    await sink.write(event)


def _resolve_run(
    run_id: str,
    run: GraphRun | None,
    *,
    deps: dict[str, Any] | None = None,
) -> GraphRun:
    """Resolve a live :class:`GraphRun` handle for ``run_id``.

    POC behavior: the in-process run registry (mapping ``run_id ->
    GraphRun``) lands in Phase 2 (task 2.30) as a lifespan singleton.
    Until then, callers must pass the live handle explicitly via the
    ``_run`` keyword. When omitted, this raises so the gap is loud
    rather than silently returning a stale snapshot from the
    checkpointer.

    The "load run from checkpoint" pattern hinted at in the design
    §9.4 step 3 (``GraphRun.from_checkpoint(...)``) does not exist on
    :class:`GraphRun` today -- the closest API is
    :meth:`GraphRun.resume`, which produces a *new* handle whose
    ``state`` starts at ``"pending"``, which is invalid for
    :meth:`GraphRun.respond` (requires ``awaiting-input``). Hence the
    explicit "registry not yet wired" error rather than a silent
    ``GraphRun.resume(...)`` shim. Same pattern as
    :func:`stargraph.serve.lifecycle._resolve_run` (task 1.22).
    """
    if run is None:
        if deps is not None:
            registry = deps.get("runs")
            if isinstance(registry, dict):
                resolved = cast("dict[str, GraphRun]", registry).get(run_id)
                if resolved is not None:
                    return resolved
        raise StargraphRuntimeError(
            f"cannot resolve live GraphRun for run_id={run_id!r}: "
            "not present in deps['runs'] registry and no _run= override "
            "supplied",
            run_id=run_id,
        )
    if run.run_id != run_id:
        raise StargraphRuntimeError(
            f"GraphRun handle run_id mismatch: expected {run_id!r}, got {run.run_id!r}",
            expected=run_id,
            actual=run.run_id,
        )
    return run


async def handle_respond(
    run_id: str,
    response: dict[str, Any],
    actor: str,
    *,
    capabilities: Capabilities | None = None,
    _run: GraphRun | None = None,
) -> RunSummary:
    """Deliver a HITL response to an ``awaiting-input`` run via :meth:`GraphRun.respond`.

    Capability check: ``runs:respond`` (engine-side, when
    ``capabilities`` is non-``None``). The HTTP route gate in
    :mod:`stargraph.serve.api` performs the same check at the FastAPI
    dependency layer; this is the second line of defense for cleared
    profiles where default-deny semantics apply (NFR-7).

    State precondition: ``run.state == "awaiting-input"``. The engine
    method also enforces this invariant (defense in depth); the outer
    check here produces a serve-layer error so the HTTP route can map
    it to a 409 Conflict per design §9.4 step 2.

    Audit emission: a serve-layer :class:`BosunAuditEvent` with
    ``fact={"kind": "respond_orchestrated", "actor": ..., "run_id": ...}``
    is constructed and best-effort persisted via the audit sink
    contextvar. The engine-internal respond audit fact (carrying
    ``actor`` + ``body_hash``) is emitted by :meth:`GraphRun.respond`
    itself (run.py:545-556).

    Returns a :class:`RunSummary` reflecting the post-respond state
    (``status="running"``). Phase 1 builds the summary inline from
    the resolved :class:`GraphRun`; Phase 2 reads the canonical row
    from the Checkpointer once the lifespan wires it.

    Refs: design §9.4 (5-step Resume flow), FR-85, AC-14.4, AC-14.5,
    AC-14.6, NFR-19 (≤2s p95 latency budget).
    """
    _check_capability(capabilities, "runs:respond", actor)
    run = _resolve_run(run_id, _run)
    if run.state != "awaiting-input":
        raise StargraphRuntimeError(
            f"respond invalid in state {run.state!r}; valid state is 'awaiting-input'",
            run_id=run_id,
            state=run.state,
        )
    await run.respond(response, actor)
    await _persist_audit(_build_audit_event(run_id=run_id, actor=actor))
    return _build_run_summary(run)


def _build_run_summary(run: GraphRun) -> RunSummary:
    """Build a :class:`RunSummary` from the live :class:`GraphRun` handle.

    POC implementation: synthesizes the six required fields from the
    in-memory handle. Phase 2 replaces this with a Checkpointer read
    so the canonical persisted row is the source of truth (matches the
    behavior of :meth:`Checkpointer.list_runs`).

    Mirrors :func:`stargraph.serve.lifecycle._build_run_summary` (task
    1.22). The status mapping folds the wider :class:`GraphRun` state
    lattice (``pending|running|paused|awaiting-input|done|cancelled|error|failed``)
    onto the narrower :class:`Checkpointer.RunSummary` Literal
    (``running|done|failed|paused``). For respond's success path the
    state is ``"running"`` (set by :meth:`GraphRun.respond` step 3),
    which maps directly; the other branches are defensive.
    """
    # Local import sidesteps the foundation/runtime cycle: serve.respond
    # is consumed by serve.api which is imported at app-factory time;
    # checkpoint.protocol is leaf-level (no stargraph.serve imports).
    from stargraph.checkpoint.protocol import RunSummary

    now = datetime.now(UTC)
    status: str
    if run.state == "cancelled":
        status = "failed"
    elif run.state == "awaiting-input":
        status = "paused"
    elif run.state in ("running", "done", "failed", "paused"):
        status = run.state
    else:
        status = "failed"
    return RunSummary(
        run_id=run.run_id,
        graph_hash=run.graph.graph_hash,
        started_at=now,
        last_step_at=now,
        status=status,  # pyright: ignore[reportArgumentType]
        parent_run_id=run.parent_run_id,
    )
