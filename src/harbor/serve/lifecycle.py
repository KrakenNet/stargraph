# SPDX-License-Identifier: Apache-2.0
"""Cancel/pause lifecycle orchestration for the serve layer (POC slice).

This module owns the request-handler-side wrapper around
:meth:`harbor.graph.GraphRun.cancel` / :meth:`harbor.graph.GraphRun.pause`.
The HTTP routes in :mod:`harbor.serve.api` (task 1.24) call into
:func:`cancel_run` / :func:`pause_run` after the FastAPI capability
gate has accepted the request; the orchestrator then performs the
*engine-side* capability check (default-deny in cleared profiles per
NFR-7), audit-emits a :class:`~harbor.runtime.events.BosunAuditEvent`,
and routes through the live :class:`GraphRun` handle to perform the
cooperative cancel/pause boundary.

POC scope (Phase 1, task 1.22):

* The "live :class:`GraphRun` handle lookup" is intentionally **stubbed**
  -- the in-process run registry that maps ``run_id -> GraphRun`` lands
  with the lifespan singleton wiring in Phase 2 (task 2.30). Until then,
  callers must pass the live ``run`` handle explicitly via the
  ``_run`` keyword (used by tests and by the API factory once it
  threads the registry through). When ``_run`` is omitted, the
  orchestrators raise :class:`HarborRuntimeError` with an explicit
  "registry not yet wired" message rather than silently returning a
  stale :class:`RunSummary`.
* The audit emission is best-effort: if the audit sink contextvar
  (:data:`harbor.serve.contextvars._audit_sink_var`) is unset (POC
  default) the :class:`BosunAuditEvent` is constructed but not
  persisted. The bus-side emission still flows through
  :meth:`GraphRun.cancel` / :meth:`GraphRun.pause` (which emit the
  typed :class:`RunCancelledEvent` / :class:`RunPausedEvent` on
  :attr:`GraphRun.bus`), so the WS stream sees the lifecycle
  transition immediately even when the audit sink is unwired.
* The capability check accepts an optional
  :class:`~harbor.security.capabilities.Capabilities` argument. When
  ``None`` (POC default; cleared deployments will pin a non-``None``
  instance), no engine-side capability enforcement happens here -- the
  HTTP route gate is the only line of defense. Phase 2 wires the real
  capabilities object into the FastAPI lifespan and threads it through
  every orchestrator call.

Design refs: §3.1 lifecycle row (`cancel_run(run_id, actor)`,
`pause_run(run_id, actor)`), §4.1 state machine (cancellable from
``running``/``paused``/``awaiting-input``; pause-able only from
``running``), §17 (lifecycle event emission contract).
FR-76, FR-77, NFR-17, NFR-18.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from harbor.errors import CapabilityError, HarborRuntimeError
from harbor.runtime.events import BosunAuditEvent
from harbor.serve.contextvars import (
    _audit_sink_var,  # pyright: ignore[reportPrivateUsage]
    _broker_var,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from nautilus import Broker  # pyright: ignore[reportMissingTypeStubs]

    from harbor.checkpoint.protocol import RunSummary
    from harbor.graph.run import GraphRun
    from harbor.security.capabilities import Capabilities
    from harbor.serve.history import RunHistory

__all__ = [
    "broker_lifespan",
    "cancel_run",
    "pause_run",
    "resolve_config_dir",
]

_LOG = logging.getLogger("harbor.serve.lifecycle")
"""Module logger; mirrors the :mod:`harbor.bosun.signing` convention so
pytest ``caplog`` fixtures pick up the missing-yaml warning. Harbor's
structlog setup ships JSON to stdout, but cleared profiles and tests
both depend on stdlib-logging interop -- the bosun module already
established this pattern (FR-49)."""


# Lifecycle audit pack identity. Phase 2 may rename to a dedicated
# ``harbor.bosun.lifecycle`` pack if the rule pack ships separately;
# for the POC the events flow under the runtime pack id so the audit
# replay can group them with the cancel/pause/respond surface emissions
# from :class:`GraphRun`.
_LIFECYCLE_PACK_ID = "harbor.runtime"
_LIFECYCLE_PACK_VERSION = "1.0"


def _check_capability(
    capabilities: Capabilities | None,
    required: str,
    actor: str,
) -> None:
    """Enforce the engine-side capability gate for a lifecycle action.

    ``capabilities=None`` is the POC default and means "no
    engine-side enforcement; rely on the HTTP route gate". When a
    :class:`Capabilities` instance is wired (cleared deployments,
    Phase 2), the call to :meth:`Capabilities.has_permission` must
    return ``True`` for ``required`` (e.g. ``"runs:cancel"``) or this
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
    kind: str,
) -> BosunAuditEvent:
    """Construct the :class:`BosunAuditEvent` for a lifecycle transition.

    ``step=0`` mirrors the convention in
    :meth:`GraphRun.cancel`/`pause`/`respond` for run-scoped (non-tick)
    emissions. The loop owns true step counters; lifecycle events are
    not tied to a node boundary so the run-scoped sentinel is correct.
    """
    now = datetime.now(UTC)
    # ProvenanceBundle (FR-55, AC-11.2): lifecycle transitions are
    # system-emitted system events (the actor name is metadata in the
    # fact body, not the provenance source); origin="system" with the
    # pack id as source mirrors the harbor.bosun.audit promotion path.
    provenance: dict[str, Any] = {
        "origin": "system",
        "source": _LIFECYCLE_PACK_ID,
        "run_id": run_id,
        "step": 0,
        "confidence": 1.0,
        "timestamp": now.isoformat(),
    }
    return BosunAuditEvent(
        run_id=run_id,
        step=0,
        ts=now,
        pack_id=_LIFECYCLE_PACK_ID,
        pack_version=_LIFECYCLE_PACK_VERSION,
        fact={"kind": kind, "actor": actor, "run_id": run_id},
        provenance=provenance,
    )


async def _persist_audit(event: BosunAuditEvent) -> None:
    """Best-effort persist a lifecycle :class:`BosunAuditEvent`.

    Reads the audit sink from
    :data:`harbor.serve.contextvars._audit_sink_var`. POC default is
    ``None`` (sink is wired by the Phase 2 lifespan factory in task
    2.30); when unwired the audit event is silently dropped at the
    persistence layer. The bus-side emission performed by
    :meth:`GraphRun.cancel`/`pause` still publishes the typed
    :class:`RunCancelledEvent` / :class:`RunPausedEvent` to the live
    WS stream regardless of audit-sink wiring.
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

    The "load run from checkpoint" pattern hinted at in the task spec
    (e.g. ``GraphRun.from_checkpoint``) does not exist on
    :class:`GraphRun` today -- the closest API is
    :meth:`GraphRun.resume`, which produces a *new* handle for a
    cold-restart resume. That handle's ``state`` starts at
    ``"pending"``, which is invalid for both :meth:`GraphRun.cancel`
    (requires ``running``/``paused``/``awaiting-input``) and
    :meth:`GraphRun.pause` (requires ``running``). Hence the explicit
    "registry not yet wired" error rather than a silent
    ``GraphRun.resume(...)`` shim.
    """
    if run is None:
        if deps is not None:
            registry = deps.get("runs")
            if isinstance(registry, dict):
                resolved = cast("dict[str, GraphRun]", registry).get(run_id)
                if resolved is not None:
                    return resolved
        raise HarborRuntimeError(
            f"cannot resolve live GraphRun for run_id={run_id!r}: "
            "not present in deps['runs'] registry and no _run= override "
            "supplied",
            run_id=run_id,
        )
    if run.run_id != run_id:
        raise HarborRuntimeError(
            f"GraphRun handle run_id mismatch: expected {run_id!r}, got {run.run_id!r}",
            expected=run_id,
            actual=run.run_id,
        )
    return run


async def cancel_run(
    run_id: str,
    actor: str,
    *,
    capabilities: Capabilities | None = None,
    run_history: RunHistory | None = None,
    _run: GraphRun | None = None,
) -> RunSummary:
    """Cooperatively cancel a live run via :meth:`GraphRun.cancel`.

    Capability check: ``runs:cancel`` (engine-side, when
    ``capabilities`` is non-``None``). The HTTP route gate in
    :mod:`harbor.serve.api` performs the same check at the FastAPI
    dependency layer; this is the second line of defense for cleared
    profiles where default-deny semantics apply.

    Audit emission: a :class:`BosunAuditEvent` with
    ``fact={"kind": "lifecycle_cancel", "actor": ..., "run_id": ...}``
    is constructed and best-effort persisted via the audit sink
    contextvar. The bus-side :class:`RunCancelledEvent` is emitted by
    :meth:`GraphRun.cancel` itself.

    When ``run_history`` is supplied (Phase-2 lifespan wiring), the
    ``runs_history`` row's ``status`` is updated to ``"cancelled"`` so
    the ``GET /runs?status=cancelled`` query path reflects the
    transition immediately (design §6.5: "When ``cancel``/``pause``...
    lands, run-history ``status`` field is updated atomically with the
    Checkpointer write").

    Returns a :class:`RunSummary` reflecting the post-cancel state.
    Phase 1 builds the summary inline from the resolved
    :class:`GraphRun`; Phase 2 reads the canonical row from the
    Checkpointer once the lifespan wires it.
    """
    _check_capability(capabilities, "runs:cancel", actor)
    run = _resolve_run(run_id, _run)
    await run.cancel(actor=actor)
    await _persist_audit(_build_audit_event(run_id=run_id, actor=actor, kind="lifecycle_cancel"))
    if run_history is not None:
        await run_history.update_status(run_id, "cancelled", finished_at=datetime.now(UTC))
    return _build_run_summary(run)


async def pause_run(
    run_id: str,
    actor: str,
    *,
    capabilities: Capabilities | None = None,
    run_history: RunHistory | None = None,
    _run: GraphRun | None = None,
) -> RunSummary:
    """Cooperatively pause a running run via :meth:`GraphRun.pause`.

    Capability check: ``runs:pause`` (engine-side, when
    ``capabilities`` is non-``None``). Audit emission: a
    :class:`BosunAuditEvent` with
    ``fact={"kind": "lifecycle_pause", "actor": ..., "run_id": ...}``.
    The bus-side :class:`RunPausedEvent` is emitted by
    :meth:`GraphRun.pause` itself.

    When ``run_history`` is supplied, the ``runs_history`` row's
    ``status`` is updated to ``"paused"`` so the ``GET /runs?status=
    paused`` query path reflects the transition. The pause is
    non-terminal (the run can be resumed) so ``finished_at`` is left
    unset.

    Returns a :class:`RunSummary` reflecting the run state at the
    moment ``pause()`` returned. Per :meth:`GraphRun.pause`, the state
    transition to ``"paused"`` happens at the loop's next checkpoint
    boundary (task 1.8) rather than synchronously here, so the summary
    may report ``"running"`` until the loop observes the pause signal.
    """
    _check_capability(capabilities, "runs:pause", actor)
    run = _resolve_run(run_id, _run)
    await run.pause(actor=actor)
    await _persist_audit(_build_audit_event(run_id=run_id, actor=actor, kind="lifecycle_pause"))
    if run_history is not None:
        await run_history.update_status(run_id, "paused")
    return _build_run_summary(run)


def _build_run_summary(run: GraphRun) -> RunSummary:
    """Build a :class:`RunSummary` from the live :class:`GraphRun` handle.

    POC implementation: synthesizes the six required fields from the
    in-memory handle. Phase 2 replaces this with a Checkpointer read
    so the canonical persisted row is the source of truth (matches the
    behavior of :meth:`Checkpointer.list_runs`).
    """
    # Local import sidesteps the foundation/runtime cycle: serve.lifecycle
    # is consumed by serve.api which is imported at app-factory time;
    # checkpoint.protocol is leaf-level (no harbor.serve imports).
    from harbor.checkpoint.protocol import RunSummary

    now = datetime.now(UTC)
    # The Checkpointer.RunSummary status Literal is
    # ``"running" | "done" | "failed" | "paused"``; map the wider
    # GraphRun state lattice (which includes ``cancelled`` /
    # ``awaiting-input`` / ``error`` / ``pending``) onto it. The
    # Phase 2 widening of the RunSummary Literal lands with the
    # checkpointer-backed lookup.
    status: str
    if run.state == "cancelled":
        # No matching Literal yet; fold to "failed" so existing
        # consumers that branch on the four-value Literal don't blow
        # up. Phase 2 widens the RunSummary Literal per task 1.2.
        status = "failed"
    elif run.state == "awaiting-input":
        status = "paused"
    elif run.state in ("running", "done", "failed", "paused"):
        status = run.state
    else:
        # ``pending``/``error`` -- map conservatively to "failed" until
        # the Phase 2 widening lands.
        status = "failed"
    return RunSummary(
        run_id=run.run_id,
        graph_hash=run.graph.graph_hash,
        started_at=now,
        last_step_at=now,
        status=status,  # pyright: ignore[reportArgumentType]
        parent_run_id=run.parent_run_id,
    )


# ====================================================================== #
# Lifespan-singleton Broker (FR-47, AC-6.1, design §8.3)                 #
# ====================================================================== #


_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "harbor"
"""Default ``<harbor-config>`` location -- overridable via ``HARBOR_CONFIG_DIR``."""

_NAUTILUS_YAML_NAME = "nautilus.yaml"
"""Filename probed under the resolved config dir for Broker construction."""


def resolve_config_dir(explicit: Path | None = None) -> Path:
    """Resolve the active ``<harbor-config>`` directory (design §8.3).

    Precedence (high → low):

    1. The ``explicit`` argument (test-supplied or programmatic override).
    2. ``HARBOR_CONFIG_DIR`` environment variable.
    3. ``~/.config/harbor/`` default.

    The returned path is *not* required to exist -- callers handle the
    "missing yaml" path themselves so the absence of a config dir is a
    soft-fail (warn + skip) rather than a startup error.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("HARBOR_CONFIG_DIR")
    if env:
        return Path(env)
    return _DEFAULT_CONFIG_DIR


def _construct_broker(yaml_path: Path) -> Broker:
    """Build a :class:`nautilus.Broker` from a resolved YAML path.

    Uses :meth:`nautilus.Broker.from_config` (the public construction
    surface; nautilus 0.1.2 does not expose a ``Broker.from_yaml``
    alias). The single-argument call accepts a path-like and returns a
    fully-wired :class:`Broker` ready for ``await broker.arequest(...)``
    -- no manual sources/registry/router assembly here (Harbor uses
    Nautilus, doesn't reimplement it -- design §8.4 constraint).
    """
    # Local import: keep nautilus out of the hot import path so unit
    # tests that don't touch the broker singleton aren't slowed down by
    # the nautilus initialisation cost.
    from nautilus import Broker  # pyright: ignore[reportMissingTypeStubs]

    return Broker.from_config(yaml_path)


@asynccontextmanager
async def broker_lifespan(
    *,
    config_dir: Path | None = None,
) -> AsyncGenerator[None]:
    """Wire the lifespan-singleton :class:`nautilus.Broker` (design §8.3).

    Resolves ``<harbor-config>`` via :func:`resolve_config_dir`, probes
    for ``<config>/nautilus.yaml``, and -- when present -- builds the
    :class:`Broker` via :meth:`Broker.from_config`. The broker is
    stashed on :data:`harbor.serve.contextvars._broker_var` for the
    duration of the context; teardown clears the var (and best-effort
    closes the broker via :meth:`Broker.aclose` when available).

    When ``nautilus.yaml`` is absent the lifespan logs a structured
    ``WARNING`` (``harbor.serve.lifecycle`` logger) and yields without
    setting the var -- consumers see :class:`HarborRuntimeError` on
    :func:`current_broker` calls. This matches the FR-47 contract:
    "Nautilus is optional, the app should still boot".

    Parameters
    ----------
    config_dir
        Optional override for the ``<harbor-config>`` directory. When
        ``None`` the resolution falls back to ``HARBOR_CONFIG_DIR`` /
        ``~/.config/harbor/`` per :func:`resolve_config_dir`.

    Yields
    ------
    None
        The lifespan context body. Typically composed inside the
        FastAPI app lifespan factory in :mod:`harbor.cli.serve`.
    """
    cfg_dir = resolve_config_dir(config_dir)
    yaml_path = cfg_dir / _NAUTILUS_YAML_NAME
    broker: Broker | None = None
    token = None
    if yaml_path.is_file():
        broker = _construct_broker(yaml_path)
        token = _broker_var.set(broker)
    else:
        _LOG.warning(
            "nautilus.yaml not found at %s; BrokerNode/broker_request will fail at first call",
            yaml_path,
        )
    try:
        yield
    finally:
        if token is not None:
            _broker_var.reset(token)
        if broker is not None:
            aclose = getattr(broker, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # pragma: no cover - teardown best-effort
                    _LOG.exception("broker aclose raised on teardown")
