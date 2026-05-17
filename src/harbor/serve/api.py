# SPDX-License-Identifier: Apache-2.0
"""Minimal FastAPI app factory for the Harbor serve surface (POC).

This module ships :func:`create_app` -- the single entry point that the
``harbor serve`` CLI subcommand (task 1.28) and the integration tests
(tasks 1.30 / 1.31 / 1.32) use to materialize the FastAPI application
with the POC route set, the bypass auth provider, and the dependency
container stashed on ``app.state.deps``.

This is the **POC slice** for spec ``harbor-serve-and-bosun``:

* **5 routes** -- ``POST /v1/runs``, ``GET /v1/runs/{run_id}``,
  ``POST /v1/runs/{run_id}/cancel``, ``POST /v1/runs/{run_id}/pause``,
  ``POST /v1/runs/{run_id}/respond``. The remaining endpoints from
  design §5.1 (``GET /v1/runs?...``, ``POST /v1/runs/{id}/resume``,
  ``POST /v1/runs/{id}/counterfactual``, ``GET /v1/runs/{id}/artifacts``,
  ``GET /v1/artifacts/{id}``, ``GET /v1/graphs``,
  ``GET /v1/registry/...``, ``WS /v1/runs/{id}/stream``,
  ``POST /webhooks/{trigger_id}``) land in Phase 2 (tasks 2.15, 2.17)
  and the WebSocket route lands separately in task 1.26.
* **Auth provider = :class:`BypassAuthProvider`** -- waves every request
  through as ``"anonymous"`` with the four POC capability grants
  (``runs:start``, ``runs:read``, ``runs:respond``, ``artifacts:write``).
  The cancel/pause routes' grants (``runs:cancel``, ``runs:pause``)
  are not in the bypass grant set yet -- a small extension to
  :class:`BypassAuthProvider`'s grant constant in task 1.18 covers them
  there; here we widen the local grant requirement to a permissive set
  that the POC bypass already grants. The real ``BearerJwtProvider``
  (Phase 2 task 2.1) tightens this.
* **Capability gate = :func:`require`** -- per design §5.4. Returns a
  FastAPI ``Depends``-wrapped callable that authenticates the request
  via the auth provider on ``app.state`` and verifies every requested
  capability is in :attr:`AuthContext.capability_grants`. A missing
  grant raises :class:`fastapi.HTTPException(403)`. The audit-emit on
  denial described in design §5.4 is deferred until the audit sink is
  wired into ``app.state`` (Phase 2 task 2.30) -- here we keep the
  factory body minimal.
* **In-memory run registry** -- ``app.state.deps`` is a plain ``dict``
  whose ``"runs"`` key holds a ``dict[str, GraphRun]`` mapping. Routes
  read this for the live :class:`~harbor.graph.GraphRun` handle that
  lifecycle / respond orchestrators require via ``_run=`` (task 1.22 /
  1.23 limitation: registry-backed lookup lands in Phase 2 task 2.30).
  The POC test harness (tasks 1.30 / 1.31 / 1.32) populates this dict
  directly before driving the routes.
* **Scheduler dependency** -- ``app.state.deps["scheduler"]`` is the
  :class:`~harbor.serve.scheduler.Scheduler` instance the lifespan
  factory builds (task 1.21). ``POST /v1/runs`` calls
  :meth:`Scheduler.enqueue` and returns ``202 Accepted`` with
  ``{run_id, status: "pending"}`` -- the awaitable future is
  *not* awaited here; the caller polls ``GET /v1/runs/{run_id}`` for
  terminal state. The POC stub :meth:`Scheduler._run_one` resolves the
  future quickly with a synthetic ``RunSummary``; production wiring
  (Phase 2) replaces the stub with the real graph driver.

Design refs: §5.1 (endpoint table), §5.4 (capability gate factory),
§3.1 (lifespan singletons). FR-12, FR-14, AC-7.1, AC-7.4, AC-13.6,
AC-13.7, AC-14.4.
"""

from __future__ import annotations

import json
import re
from datetime import datetime  # noqa: TC003 -- runtime use by FastAPI Query
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field

from harbor.errors import ArtifactNotFound, BroadcasterOverflow, HarborRuntimeError
from harbor.ir import dumps as ir_dumps
from harbor.replay.counterfactual import (
    CounterfactualMutation,  # noqa: TC001 -- pydantic resolves at runtime in the request model
)
from harbor.serve.auth import AuthContext, AuthProvider, BypassAuthProvider
from harbor.serve.lifecycle import cancel_run, pause_run
from harbor.serve.ratelimit import (
    DEFAULT_REFILL_PER_MINUTE,
    PerActorBucketRegistry,
)
from harbor.serve.respond import handle_respond

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from harbor.artifacts.base import ArtifactRef, ArtifactStore
    from harbor.checkpoint.protocol import Checkpointer, RunSummary
    from harbor.graph.run import GraphRun
    from harbor.serve.broadcast import EventBroadcaster
    from harbor.serve.history import RunHistory, RunRecord
    from harbor.serve.profiles import Profile
    from harbor.serve.scheduler import Scheduler


__all__ = ["create_app", "require"]


# --- WS resume cursor (task 2.21, design §17 Decision #3) --------------------

#: Strict cursor shape ``{run_id}:{step}:{seq_within_step}`` -- run_id must be
#: alphanumeric/dash/underscore; step + seq_within_step are non-negative ints.
#: Anything outside this regex is rejected with WS close 1008 (policy
#: violation) per task 2.21 constraints.
_LAST_EVENT_ID_RE = re.compile(r"^([A-Za-z0-9_-]+):(\d+):(\d+)$")


def _parse_last_event_id(raw: str) -> tuple[str, int, int] | None:
    """Strict parser for ``last_event_id`` query values.

    Returns ``(run_id, step, seq_within_step)`` on success, ``None`` on
    malformed input. Rejects empty strings, missing colons, non-digit
    components -- the WS handler closes 1008 on ``None``. Don't trust
    the client; the cursor is part of the URL surface.
    """
    match = _LAST_EVENT_ID_RE.match(raw)
    if match is None:
        return None
    return match.group(1), int(match.group(2)), int(match.group(3))


def _audit_path_for_run(deps: dict[str, Any], run_id: str) -> Path | None:
    """Resolve the per-run JSONL audit path from the deps container.

    Two resolution rungs (in order):

    1. ``deps["audit_path"]`` -- explicit override (test harness or
       lifespan factory). When set, the same file is the audit log for
       every run; replay scans it filtered on ``run_id``.
    2. ``deps["run_history"].jsonl_audit_path`` -- the
       :class:`~harbor.serve.history.RunHistory` instance the lifespan
       factory wires (see :mod:`harbor.cli.serve`).

    Returns ``None`` when neither rung resolves -- the WS handler then
    closes 1008 with reason "audit not found" rather than tail-spinning
    on a non-existent file.
    """
    explicit = deps.get("audit_path")
    if explicit is not None:
        return cast("Path", explicit)
    run_history: Any | None = deps.get("run_history")
    if run_history is None:
        return None
    path: Path | None = getattr(run_history, "jsonl_audit_path", None)
    return path


def _replay_audit_after_cursor(
    audit_path: Path,
    run_id: str,
    step_cursor: int,
    seq_cursor: int,
) -> list[dict[str, Any]]:
    """Read JSONL audit file and return every event strictly after the cursor.

    Walks the file forward (positional scan per design §17 Decision #3),
    decodes each line as JSON, unwraps the optional signed envelope
    (``{"event": ..., "sig": "..."}``), filters to events with
    ``run_id == run_id``, and emits those whose
    ``(step, seq_within_step)`` is strictly greater than the cursor.
    ``seq_within_step`` is implicit: the 0-indexed ordinal of the event
    within the ``(run_id, step)`` group as written to JSONL.

    Returns the list of event payload dicts (already JSON-mode shape) so
    the WS handler can ``ir_dumps``-equivalent re-serialize them or
    just send the raw line. We send the dict via ``json.dumps`` to keep
    a single hot path.

    Skipped silently:
    * Blank lines (between rotated segments).
    * Malformed JSON (truncated tails, non-dict payloads).
    * Events missing ``run_id`` / ``step`` (corrupted records).
    """
    out: list[dict[str, Any]] = []
    if not audit_path.exists():
        return out
    seq_per_step: dict[int, int] = {}
    with audit_path.open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                record_any: Any = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(record_any, dict):
                continue
            record = cast("dict[str, Any]", record_any)
            payload_any: Any = record.get("event", record)
            if not isinstance(payload_any, dict):
                continue
            payload = cast("dict[str, Any]", payload_any)
            ev_run_id = payload.get("run_id")
            ev_step = payload.get("step")
            if not isinstance(ev_run_id, str) or not isinstance(ev_step, int):
                continue
            if ev_run_id != run_id:
                continue
            seq = seq_per_step.get(ev_step, 0)
            seq_per_step[ev_step] = seq + 1
            # Strict ``>`` comparison: cursor points at an already-seen
            # event; replay starts at the next one (positional event_id+1
            # per design §5.6).
            if (ev_step, seq) > (step_cursor, seq_cursor):
                out.append(payload)
    return out


# --- Request / response models -----------------------------------------------


class _StartRunRequest(BaseModel):
    """Body for ``POST /v1/runs``.

    ``params`` is an open dict for the POC -- production wiring pins
    this to the IR's parameter schema once the registry lands (Phase 2
    task 2.17). ``idempotency_key`` is forwarded to the Scheduler but
    ignored in the POC path (Scheduler has no Checkpointer-backed
    pending state yet -- task 2.13).
    """

    graph_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class _StartRunResponse(BaseModel):
    """Response body for ``POST /v1/runs``."""

    run_id: str
    status: str


class _RespondRequest(BaseModel):
    """Body for ``POST /v1/runs/{run_id}/respond``."""

    response: dict[str, Any]


class _RunSummaryItem(BaseModel):
    """One row of the ``GET /v1/runs`` paginated response (design §5.1).

    Mirrors the ``runs_history`` row shape from
    :class:`harbor.serve.history.RunRecord` but exposed as the
    canonical ``RunSummary`` API surface (design §5.1 ``GET /v1/runs``
    returns ``Page[RunSummary]``). Distinct from
    :class:`harbor.checkpoint.protocol.RunSummary` (which is the
    ``Checkpointer.list_runs`` row with a narrower status Literal); the
    history-backed view here carries the wider lifecycle states
    (``pending``, ``cancelled``, ``error``) plus ``trigger_source`` /
    ``duration_ms`` columns the Checkpointer summary lacks. Phase 3
    may unify the two once the checkpointer's status Literal widens.
    """

    run_id: str
    status: str
    graph_hash: str
    trigger_source: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    parent_run_id: str | None = None


class _RunsPage(BaseModel):
    """Response body for ``GET /v1/runs`` (design §5.1 ``Page[RunSummary]``).

    Simple offset+limit pagination per design §6.5 ("max ``limit=100``")
    -- the canonical cursor-based API is deferred to Phase 3 (the
    ``cursor=`` query param in design §6.5). The ``total`` field is a
    ``SELECT COUNT(*)`` with the same filter; fine for POC scale, may
    switch to an estimate-or-omit pattern in Phase 3 for very large
    history tables.
    """

    items: list[_RunSummaryItem]
    total: int
    limit: int
    offset: int
    cursor: str | None = None


class _ResumeRequest(BaseModel):
    """Body for ``POST /v1/runs/{run_id}/resume`` (design §5.1).

    ``from_step`` is optional: when ``None`` the engine resumes from the
    latest persisted checkpoint (the common case); when set it pins the
    resume to a specific step (FR-19 replay determinism).
    """

    from_step: int | None = None


class _CounterfactualRequest(BaseModel):
    """Body for ``POST /v1/runs/{run_id}/counterfactual`` (design §5.1, §5.5).

    ``mutation`` is the structured :class:`CounterfactualMutation`
    builder (state_overrides / facts_assert / facts_retract /
    rule_pack_version / node_output_overrides). ``step`` pins the
    cf-fork point on the parent run (FR-27 design §3.8.4 step 1).
    ``reason`` is a free-form audit string captured into the cf-emit
    audit envelope (design §5.1 audit column).
    """

    step: int
    mutation: CounterfactualMutation
    reason: str = ""


class _GraphSummary(BaseModel):
    """One row of the ``GET /v1/graphs`` response (design §5.1).

    POC: the in-memory ``app.state.deps["graphs"]`` dict is a
    ``dict[str, Graph]`` keyed by ``graph_id``; this row exposes
    ``graph_id`` + ``graph_hash`` since those are the two fields every
    caller (CLI inspect, Bosun pack manifest, audit replay) reads.
    Phase 3 polish will widen this to the full design §3.5 graph
    metadata once the registry-backed lookup lands.
    """

    graph_id: str
    graph_hash: str


# --- Capability gate factory (design §5.4) -----------------------------------


def require(*capabilities: str) -> Callable[..., Any]:
    """Build a FastAPI dependency that gates a route on capability grants.

    Per design §5.4 + task 2.36 (FR-32, FR-69, AC-4.1, design §11.1),
    each route declares the capabilities it needs and the dependency:

    1. Calls the auth provider (from ``app.state.auth_provider``) to
       authenticate the request and obtain an :class:`AuthContext`.
    2. Verifies every required capability against
       :attr:`AuthContext.capability_grants` under the active profile's
       :attr:`Profile.default_deny_capabilities` flag:

       * **Cleared profile** (``default_deny_capabilities=True``):
         every required capability MUST appear in the grant set; an
         unset capability raises :class:`HTTPException(403)` with the
         message ``"capability '<cap>' not granted under cleared
         profile"``. This is the locked-design §11.1 default-deny
         contract; the 7 routes that flag "cleared default-deny" are
         ``runs:cancel``, ``runs:pause``, ``runs:respond``,
         ``runs:counterfactual``, ``artifacts:read``,
         ``artifacts:write``, ``tools:broker_request``. (The remaining
         ``runs:start`` / ``runs:read`` are permissive in both
         profiles.)
       * **OSS-default profile** (``default_deny_capabilities=False``):
         an unset capability is permissive -- the request flows through
         to the handler. Phase-1 behavior preserved.

    3. Returns the :class:`AuthContext` so route handlers can read
       ``actor`` for audit emission and ``session_id`` for run
       correlation.

    Audit-emit on denial (design §5.4 line 515, FR-65, AC-8.5):
    when ``app.state.deps["audit_sink"]`` is wired, the gate emits a
    :class:`~harbor.runtime.events.BosunAuditEvent` with
    ``fact={"kind": "capability_denied", "actor": ..., "capability":
    ..., "route": ...}`` *before* raising the 403. Best-effort: a
    missing or unwired sink is silently skipped (the 403 still
    fires). This is the defense-in-depth complement to the engine-side
    capability check in :mod:`harbor.serve.lifecycle` /
    :mod:`harbor.serve.respond`, which only runs when the cleared
    profile pins a non-``None`` :class:`Capabilities` instance.

    The returned callable is intentionally NOT pre-wrapped with
    :class:`fastapi.Depends`; route handlers call ``Depends(require(...))``
    at the use site so each invocation builds an independent dependency
    closure. This matches the design §5.4 example (``Depends(require("runs:cancel"))``)
    and FastAPI's idiomatic Annotated-Depends pattern.
    """

    async def _dep(request: Request) -> AuthContext:
        provider: AuthProvider = request.app.state.auth_provider
        ctx = await provider.authenticate(request)
        # Profile-conditional default-deny (task 2.36, FR-32, AC-4.1).
        # ``app.state.profile`` is set by ``create_app``; ad-hoc apps
        # built without it fall back to permissive (no-deny).
        profile: Profile | None = getattr(request.app.state, "profile", None)
        default_deny = bool(
            getattr(profile, "default_deny_capabilities", False),
        )
        for cap in capabilities:
            if cap not in ctx["capability_grants"] and default_deny:
                # Audit-emit on denial (FR-65, AC-8.5). Best-effort:
                # a missing sink is silent. Read at the use-site so a
                # late-wired sink (lifespan factory) is picked up
                # without re-entering the gate.
                await _emit_capability_denied_audit(
                    request,
                    actor=ctx["actor"],
                    capability=cap,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(f"capability '{cap}' not granted under cleared profile"),
                )
            # OSS-default permissive fallthrough (Phase-1 behavior).
        return ctx

    return _dep


async def _emit_capability_denied_audit(
    request: Request,
    *,
    actor: str,
    capability: str,
) -> None:
    """Best-effort emit a ``capability_denied`` :class:`BosunAuditEvent`.

    Reads the audit sink from ``app.state.deps["audit_sink"]`` (the
    Phase-2 lifespan-singleton convention; matches the
    :mod:`harbor.serve.lifecycle` ``_persist_audit`` resolution).
    Silently no-ops when:

    * ``deps`` is missing on ``app.state`` (ad-hoc test apps),
    * ``deps["audit_sink"]`` is unset (POC default),
    * sink ``write`` raises (audit emission MUST NOT mask the 403).

    The audit fact carries ``kind="capability_denied"``, ``actor``,
    ``capability``, and ``route`` (the request URL path) so a downstream
    SIEM can correlate the denial with the offending caller.
    """
    deps_raw = getattr(request.app.state, "deps", None)
    if not isinstance(deps_raw, dict):
        return
    deps = cast("dict[str, Any]", deps_raw)
    sink: Any = deps.get("audit_sink")
    if sink is None:
        return
    # Local import: BosunAuditEvent lives in harbor.runtime.events; the
    # module-level import would tie every serve.api consumer to the
    # full Pydantic event union for a path that's only hit on denial.
    from datetime import UTC, datetime

    from harbor.runtime.events import BosunAuditEvent

    now = datetime.now(UTC)
    # ProvenanceBundle (FR-55, AC-11.2): capability denials are
    # system-emitted at the HTTP gate; origin="system" with pack id
    # as source mirrors the lifecycle/respond conventions.
    provenance: dict[str, Any] = {
        "origin": "system",
        "source": "harbor.serve.gate",
        "run_id": "",
        "step": 0,
        "confidence": 1.0,
        "timestamp": now.isoformat(),
    }
    event = BosunAuditEvent(
        run_id="",
        step=0,
        ts=now,
        pack_id="harbor.serve.gate",
        pack_version="1.0",
        fact={
            "kind": "capability_denied",
            "actor": actor,
            "capability": capability,
            "route": request.url.path,
        },
        provenance=provenance,
    )
    try:
        await sink.write(event)
    except Exception:
        # Best-effort: audit emission must not mask the 403. A sink
        # failure is loud in logs but does not block the security
        # boundary -- the caller still raises HTTPException(403).
        import logging

        logging.getLogger("harbor.serve.api").warning(
            "capability-denied audit emission failed for actor=%r capability=%r",
            actor,
            capability,
            exc_info=True,
        )


# --- App factory --------------------------------------------------------------


def _resolve_run_registry(deps: dict[str, Any]) -> dict[str, GraphRun]:
    """Pull (or lazily create) the in-memory ``run_id -> GraphRun`` registry.

    POC: lives in ``deps["runs"]``. Phase 2 task 2.30 replaces this with
    a Checkpointer-backed lookup (canonical persisted row + live handle
    map) and threads it through a proper container instead of an open
    dict.
    """
    runs: dict[str, GraphRun] | None = deps.get("runs")
    if runs is None:
        runs = {}
        deps["runs"] = runs
    return runs


def _resolve_broadcaster_registry(
    deps: dict[str, Any],
) -> dict[str, EventBroadcaster]:
    """Pull (or lazily create) the in-memory ``run_id -> EventBroadcaster`` map.

    POC: parallels :func:`_resolve_run_registry`; the WS route handler
    looks up a per-run :class:`~harbor.serve.broadcast.EventBroadcaster`
    here. Phase 2 (task 2.30) folds this into the Checkpointer-backed
    run-handle container; for now the lifespan / test harness populates
    this dict directly when a run is registered.
    """
    bcs: dict[str, EventBroadcaster] | None = deps.get("broadcasters")
    if bcs is None:
        bcs = {}
        deps["broadcasters"] = bcs
    return bcs


def create_app(
    profile: Profile,
    deps: dict[str, Any],
    lifespan: Callable[[FastAPI], Any] | None = None,
) -> FastAPI:
    """Build the Harbor serve FastAPI app with the POC route set.

    Parameters
    ----------
    profile:
        The active deployment :class:`~harbor.serve.profiles.Profile`
        (``oss-default`` or ``cleared``). Stashed on ``app.state.profile``
        so route handlers and dependencies can read profile-conditional
        policy (e.g. cleared default-deny on cancel/pause routes per
        design §5.1). The POC routes don't branch on profile yet -- the
        capability gate factory above is profile-agnostic; cleared
        deployments tighten the gate by pinning a stricter
        :class:`Capabilities` on the engine side.
    deps:
        Dependency container -- a plain dict the lifespan factory
        populates with the live :class:`Scheduler`,
        :class:`Checkpointer`, :class:`ArtifactStore`, audit sink, etc.
        Stashed on ``app.state.deps`` for route-handler access. POC
        routes consult three keys:

        * ``deps["scheduler"]`` -- the :class:`Scheduler` instance.
          Required by ``POST /v1/runs``.
        * ``deps["runs"]`` -- the ``run_id -> GraphRun`` in-memory
          registry. Lazily created on first read. Required by the
          GET / cancel / pause / respond routes.
        * ``deps["capabilities"]`` -- optional engine-side
          :class:`~harbor.security.capabilities.Capabilities` instance
          threaded into the lifecycle / respond orchestrators. POC
          default is missing (-> ``None``) so engine-side enforcement
          is a no-op; cleared deployments pin a non-``None`` value.
    lifespan:
        Optional FastAPI lifespan callable (``@asynccontextmanager``-style
        ``async def lifespan(app): yield``). When provided, FastAPI runs
        the body on startup, yields control during request handling, and
        runs the post-yield block on shutdown. The CLI ``harbor serve``
        boot path (task 1.28 + VE2-1 fix) passes a small lifespan that
        starts/stops the in-process :class:`Scheduler` so
        ``POST /v1/runs`` returns a structured 202 instead of 500-on-
        ``KeyError``. Phase 2 task 2.30 replaces this with the full
        lifespan singleton wiring (Scheduler + Checkpointer +
        ArtifactStore + audit sink). Default ``None`` keeps the
        existing in-process test-harness path (``TestClient`` /
        ``httpx.AsyncClient``) untouched.

    Returns
    -------
    FastAPI
        Configured app with the 5 POC routes registered.

    POC limitations (each Phase 2 work item is signposted):

    * WebSocket ``/v1/runs/{id}/stream`` is registered (task 1.26) but
      runs without auth and without ``?last_event_id`` resume (Phase 2
      task 2.5 / Resolved Decision #10).
    * No ``GET /v1/runs?...`` listing route (task 2.15).
    * No counterfactual / artifacts / graphs / registry routes
      (task 2.17).
    * No CORS / TLS / OpenAPI extras (task 2.5 wires profile->TLS;
      task 2.19 emits the canonical OpenAPI spec).
    * 404 for missing runs and 422 for malformed bodies are FastAPI's
      default behavior; we don't customize the error envelope yet.
    """
    from harbor import __version__ as _harbor_version

    app = FastAPI(
        title="harbor",
        version=_harbor_version,
        description=(
            "Harbor serve API (POC slice -- design §5.1 minimal subset). "
            "5 routes: POST /v1/runs, GET /v1/runs/{run_id}, "
            "POST /v1/runs/{run_id}/cancel, POST /v1/runs/{run_id}/pause, "
            "POST /v1/runs/{run_id}/respond. Remaining endpoints land in "
            "Phase 2 (tasks 2.15, 2.17) and the WS route in task 1.26."
        ),
        lifespan=lifespan,
    )

    # Stash dependency container + profile + auth provider on app.state.
    # ``app.state`` is FastAPI's idiomatic per-app singleton holder
    # (request handlers read via ``request.app.state.<key>``).
    #
    # Auth-provider selection (task 2.5, design §11.1, §17 Decision #5):
    # if ``profile.auth_provider_factory`` is set, we call it now to
    # construct the provider instance and stash on ``app.state``. The
    # ``OssDefaultProfile`` and ``ClearedProfile`` defaults wire
    # ``BypassAuthProvider`` and ``MtlsProvider`` factories respectively
    # (cleared honors a ``harbor.toml`` ``[serve.cleared].auth_provider``
    # override per locked Decision #5). Profiles built ad-hoc by tests
    # may leave the factory as ``None``; in that case we fall back to
    # ``BypassAuthProvider()`` so the engine integration tests that
    # construct a ``Profile()`` directly keep working. POC: factory
    # construction happens at app creation (synchronous, no I/O); when
    # Phase 3 wires JWKS URL discovery / API-key store loading the
    # factory call may need to move into the lifespan body.
    app.state.profile = profile
    app.state.deps = deps
    if profile.auth_provider_factory is not None:
        app.state.auth_provider = profile.auth_provider_factory()
    else:
        app.state.auth_provider = BypassAuthProvider()

    # Wire the OpenAPI 3.1 generator (design §5.3, task 2.19). FastAPI
    # invokes ``app.openapi()`` on the first ``GET /openapi.json`` /
    # ``GET /docs`` hit; our override regenerates the augmented spec
    # (FastAPI base + IR Pydantic component merge) every call so
    # routes added by tests after ``create_app`` are picked up. The
    # cost is negligible (one ``get_openapi(...)`` call + a 5-key
    # dict merge); production deployments may switch to a cached
    # variant via ``scripts/regen_openapi.py`` (Phase 4) if profiling
    # shows hot-path impact.
    from harbor.serve.openapi import regen_openapi_spec

    app.openapi = lambda: regen_openapi_spec(app)

    # Seed the per-actor counterfactual rate-limiter (design §5.5,
    # locked Decision #6). The registry is process-local in-memory; a
    # restart resets every bucket to ``capacity`` (brief burst window
    # documented in :mod:`harbor.serve.ratelimit`). Skips if the caller
    # already wired one (test harness override) and reads the
    # capacity / refill defaults from ``deps``; future wiring (task
    # 2.5) will source the per-minute knob from
    # ``harbor.toml: counterfactual.rate_limit_per_min``.
    if "counterfactual_rate_limiter" not in deps:
        rl_per_min = deps.get(
            "counterfactual_rate_limit_per_min",
            DEFAULT_REFILL_PER_MINUTE,
        )
        deps["counterfactual_rate_limiter"] = PerActorBucketRegistry(
            capacity=int(rl_per_min),
            refill_per_minute=int(rl_per_min),
        )

    # --- POC routes ----------------------------------------------------------
    #
    # Capability-gate dependencies are declared inline at each route's
    # ``Annotated[..., Depends(require(<cap>))]`` parameter rather than
    # via reusable type aliases -- ``Annotated`` aliases assigned inside
    # a function body are not valid type forms under pyright's strict
    # checking (``reportInvalidTypeForm``). Module-level aliases would
    # work but force every route to re-import them; inline declarations
    # are the simpler POC choice.

    @app.post(
        "/v1/runs",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=_StartRunResponse,
    )
    async def start_run(  # pyright: ignore[reportUnusedFunction]
        body: _StartRunRequest,
        _ctx: Annotated[AuthContext, Depends(require("runs:start"))],
    ) -> _StartRunResponse:
        """Enqueue a new run; return ``202 Accepted`` with the run handle.

        ``handle.run_id`` is the Scheduler-derived canonical id from
        ``(graph_id, idempotency_key)``; callers poll ``GET
        /v1/runs/{run_id}`` for terminal state.
        """
        scheduler: Scheduler = app.state.deps["scheduler"]
        handle = scheduler.enqueue(
            graph_id=body.graph_id,
            params=body.params,
            idempotency_key=body.idempotency_key,
        )
        return _StartRunResponse(
            run_id=handle.run_id,
            status="pending",
        )

    @app.get("/v1/runs", response_model=_RunsPage)
    async def list_runs(  # pyright: ignore[reportUnusedFunction]
        _ctx: Annotated[AuthContext, Depends(require("runs:read"))],
        status_: Annotated[
            str | None,
            Query(
                alias="status",
                description=(
                    "Filter by run status (e.g. ``pending``, ``running``, "
                    "``done``, ``failed``, ``cancelled``, ``paused``)."
                ),
            ),
        ] = None,
        since: Annotated[
            datetime | None,
            Query(
                description=("Inclusive lower bound on ``started_at`` (ISO-8601)."),
            ),
        ] = None,
        until: Annotated[
            datetime | None,
            Query(
                description=("Exclusive upper bound on ``started_at`` (ISO-8601)."),
            ),
        ] = None,
        trigger_source: Annotated[
            str | None,
            Query(
                description=("Filter by trigger source (``manual``, ``cron``, ``webhook``)."),
            ),
        ] = None,
        limit: Annotated[
            int,
            Query(
                ge=1,
                le=1000,
                description=(
                    "Page size; capped at 1000. Design §6.5 caps the "
                    "default at 100; values above that are accepted but "
                    "subject to scan cost."
                ),
            ),
        ] = 100,
        offset: Annotated[
            int,
            Query(
                ge=0,
                description=(
                    "Offset into the filtered result set. Phase 3 may "
                    "switch to keyset / cursor pagination for stability "
                    "under retention sweeps."
                ),
            ),
        ] = 0,
    ) -> _RunsPage:
        """Return a paginated list of run-history rows (design §5.1).

        Backed by :meth:`harbor.serve.history.RunHistory.list` --
        ``app.state.deps["run_history"]`` is the canonical reference
        wired by the lifespan factory (CLI ``harbor serve`` boot path).
        When ``run_history`` is missing from the deps container the
        route returns an empty page rather than 500ing -- a permissive
        POC default that lets ad-hoc test ``create_app(profile,
        deps={})`` calls hit the route without lifespan wiring. The
        cleared profile may want to flip this to a stricter "503
        Service Unavailable" once the Phase 3 lifespan wiring is the
        only supported path.

        Filter clauses compose with ``AND``; ``trigger_source`` is
        validated by the underlying SQL CHECK constraint -- an unknown
        value just yields zero rows. ``status`` is free-form (the
        ``runs_history.status`` column is unconstrained TEXT to support
        the wider state lattice). Page total is computed via a
        ``SELECT COUNT(*)`` with the same filter (POC scale; design
        §6.5 cursor pagination lands in Phase 3).
        """
        run_history: RunHistory | None = app.state.deps.get("run_history")
        if run_history is None:
            return _RunsPage(items=[], total=0, limit=limit, offset=offset)
        # ``trigger_source`` is typed ``str | None`` on the route param
        # so FastAPI can render the OpenAPI schema cleanly; the
        # underlying ``RunHistory.list`` narrows to the
        # ``TriggerSource`` Literal -- we forward the raw value and let
        # SQL's CHECK constraint reject invalid values as zero rows.
        records: list[RunRecord] = await run_history.list(
            status=status_,
            since=since,
            until=until,
            trigger_source=trigger_source,  # pyright: ignore[reportArgumentType]
            limit=limit,
            offset=offset,
        )
        total = await run_history.count(
            status=status_,
            since=since,
            until=until,
            trigger_source=trigger_source,  # pyright: ignore[reportArgumentType]
        )
        items = [
            _RunSummaryItem(
                run_id=r.run_id,
                status=r.status,
                graph_hash=r.graph_hash,
                trigger_source=r.trigger_source,
                started_at=r.started_at,
                finished_at=r.finished_at,
                duration_ms=r.duration_ms,
                parent_run_id=r.parent_run_id,
            )
            for r in records
        ]
        return _RunsPage(items=items, total=total, limit=limit, offset=offset)

    @app.get("/v1/runs/{run_id}")
    async def get_run(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        _ctx: Annotated[AuthContext, Depends(require("runs:read"))],
    ) -> dict[str, Any]:
        """Return the :class:`RunSummary` for ``run_id`` or 404.

        POC: reads the in-memory ``run_id -> GraphRun`` registry on
        ``app.state.deps["runs"]``. Phase 2 task 2.30 reads from the
        Checkpointer instead. Returns the live handle's snapshot
        synthesized into the :class:`RunSummary` shape (status mapped
        per the same lattice fold used in
        :mod:`harbor.serve.lifecycle`).
        """
        runs = _resolve_run_registry(app.state.deps)
        run: GraphRun | None = runs.get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id!r} not found",
            )
        return _summarize(run).model_dump(mode="json")

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_run_route(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        ctx: Annotated[AuthContext, Depends(require("runs:cancel"))],
    ) -> dict[str, Any]:
        """Cooperatively cancel a live run; return updated :class:`RunSummary`.

        Routes through :func:`harbor.serve.lifecycle.cancel_run` for
        the engine-side capability check + audit emission + bus-side
        :class:`RunCancelledEvent`. Missing run -> 404. Engine-side
        :class:`HarborRuntimeError` (e.g. cancel-from-terminal-state)
        bubbles up as 400 by FastAPI default; production wiring
        (Phase 2) maps these to a canonical error envelope.
        """
        runs = _resolve_run_registry(app.state.deps)
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id!r} not found",
            )
        try:
            summary = await cancel_run(
                run_id,
                ctx["actor"],
                capabilities=app.state.deps.get("capabilities"),
                _run=run,
            )
        except HarborRuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return summary.model_dump(mode="json")

    @app.post("/v1/runs/{run_id}/pause")
    async def pause_run_route(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        ctx: Annotated[AuthContext, Depends(require("runs:pause"))],
    ) -> dict[str, Any]:
        """Cooperatively pause a running run; return updated :class:`RunSummary`."""
        runs = _resolve_run_registry(app.state.deps)
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id!r} not found",
            )
        try:
            summary = await pause_run(
                run_id,
                ctx["actor"],
                capabilities=app.state.deps.get("capabilities"),
                _run=run,
            )
        except HarborRuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return summary.model_dump(mode="json")

    @app.websocket("/v1/runs/{run_id}/stream")
    async def stream_run_events(  # pyright: ignore[reportUnusedFunction]
        websocket: WebSocket,
        run_id: str,
        last_event_id: Annotated[str | None, Query()] = None,
    ) -> None:
        """Stream typed :class:`Event` JSON frames for a live run.

        Subscribes to the per-run
        :class:`~harbor.serve.broadcast.EventBroadcaster` registered on
        ``app.state.deps["broadcasters"]`` and forwards each yielded
        :class:`~harbor.runtime.events.Event` to the WebSocket as a
        canonical-IR text frame (``ir_dumps`` per FR-15, AC-11.5).

        Resume cursor (task 2.21, design §17 Decision #3): when the
        client supplies ``?last_event_id=<run_id>:<step>:<seq>``, the
        handler replays the JSONL audit file forward from
        ``event_id+1`` (positional scan over the per-run log) before
        yielding live events. ``seq_within_step`` is the 0-indexed
        ordinal of the event within the ``(run_id, step)`` group as
        written to JSONL. Malformed cursors close 1008 (policy
        violation); a missing audit file (replay path required by the
        cursor but not yet flushed) also closes 1008 with reason "audit
        not found" rather than tail-spinning.

        Disconnect-on-overflow (task 2.21): the broadcaster's
        per-subscriber bounded buffer (size 100 per design §5.6)
        overflows into :class:`~harbor.errors.BroadcasterOverflow`; the
        handler catches it and closes 1011 with reason "slow consumer".

        POC limitations:

        * No auth on the WS handshake -- the cleared profile would
          require a bearer token here (Phase 2 task 2.5 wiring).
        * Missing run -> close with 1008 (policy violation).
        * Per-message-deflate: a uvicorn-level flag (``--ws-per-message-
          deflate=False``); see :mod:`harbor.cli.serve` for the
          programmatic uvicorn config wiring (TODO 2.21 -- left as a
          follow-up since the POC ``uvicorn.run(...)`` call has no
          config-object surface yet).
        """
        broadcasters = _resolve_broadcaster_registry(app.state.deps)
        broadcaster = broadcasters.get(run_id)
        if broadcaster is None:
            # Accept first so the client receives the close frame with
            # a structured reason (Starlette closes with HTTP 403 if we
            # close before accept, which clients see as a handshake
            # failure rather than an application-level rejection).
            await websocket.accept()
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"run {run_id!r} not found",
            )
            return

        # Parse + validate the resume cursor (if any) BEFORE accepting,
        # so a malformed value gets a clean 1008 close. Don't trust the
        # client.
        cursor: tuple[str, int, int] | None = None
        if last_event_id is not None:
            cursor = _parse_last_event_id(last_event_id)
            if cursor is None:
                await websocket.accept()
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason=f"malformed last_event_id: {last_event_id!r}",
                )
                return
            cur_run_id, _, _ = cursor
            if cur_run_id != run_id:
                await websocket.accept()
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason=(
                        f"last_event_id run_id mismatch: cursor={cur_run_id!r} path={run_id!r}"
                    ),
                )
                return

        await websocket.accept()

        # --- Phase 1: replay JSONL audit from cursor+1 (if cursor set) -------
        if cursor is not None:
            audit_path = _audit_path_for_run(app.state.deps, run_id)
            if audit_path is None or not audit_path.exists():
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="audit not found",
                )
                return
            _, step_cursor, seq_cursor = cursor
            replayed = _replay_audit_after_cursor(audit_path, run_id, step_cursor, seq_cursor)
            for payload in replayed:
                await websocket.send_text(json.dumps(payload))

        # --- Phase 2: subscribe to live broadcaster --------------------------
        try:
            async for event in broadcaster.subscribe():
                await websocket.send_text(ir_dumps(event))
        except BroadcasterOverflow:
            # Per task 2.21: hard-coded disconnect-on-overflow.
            await websocket.close(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="slow consumer",
            )
            return
        except WebSocketDisconnect:
            # Client tore down the connection; the broadcaster's
            # shielded teardown removes our subscriber send half on
            # iterator exit (design §5.6).
            return

    @app.post("/v1/runs/{run_id}/respond")
    async def respond_route(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        body: _RespondRequest,
        ctx: Annotated[AuthContext, Depends(require("runs:respond"))],
    ) -> dict[str, Any]:
        """Deliver a HITL response to an ``awaiting-input`` run.

        Routes through :func:`harbor.serve.respond.handle_respond` for
        the engine-side capability check + state-precondition check +
        audit emission. Missing run -> 404; non-``awaiting-input``
        state -> 409 Conflict (per design §9.4 step 2).
        """
        runs = _resolve_run_registry(app.state.deps)
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run {run_id!r} not found",
            )
        try:
            summary = await handle_respond(
                run_id,
                body.response,
                ctx["actor"],
                capabilities=app.state.deps.get("capabilities"),
                _run=run,
            )
        except HarborRuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return summary.model_dump(mode="json")

    @app.post(
        "/v1/runs/{run_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=_StartRunResponse,
    )
    async def resume_run_route(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        body: _ResumeRequest,
        _ctx: Annotated[AuthContext, Depends(require("runs:resume"))],
    ) -> _StartRunResponse:
        """Resume a paused run via :meth:`GraphRun.resume` (design §5.1).

        POC: returns ``202 Accepted`` with ``status="pending"`` once
        :meth:`GraphRun.resume` materializes a fresh :class:`GraphRun`
        bound to the same ``run_id`` (FR-19, FR-20). The caller polls
        ``GET /v1/runs/{run_id}`` for terminal state. The freshly
        materialized handle is registered into ``deps["runs"]`` so
        subsequent lifecycle calls (cancel/pause/respond) find it.

        Phase 2 task 2.30 wires this into the Scheduler's enqueue path
        so resumed runs honor the per-``graph_hash`` concurrency
        limiter; the POC drives ``GraphRun.resume`` directly off the
        Checkpointer in ``deps["checkpointer"]`` since the live run
        registry rehydration step is Phase 2 work.
        """
        from harbor.graph.run import GraphRun

        checkpointer: Checkpointer | None = app.state.deps.get("checkpointer")
        if checkpointer is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="checkpointer not wired in deps",
            )
        try:
            resumed = await GraphRun.resume(
                checkpointer,
                run_id,
                from_step=body.from_step,
            )
        except HarborRuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        runs = _resolve_run_registry(app.state.deps)
        runs[run_id] = resumed
        return _StartRunResponse(run_id=run_id, status="pending")

    @app.post("/v1/runs/{run_id}/counterfactual")
    async def counterfactual_route(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        body: _CounterfactualRequest,
        ctx: Annotated[
            AuthContext,
            Depends(require("counterfactual:run")),
        ],
    ) -> dict[str, Any]:
        """Fork a counterfactual child run from ``run_id`` at ``body.step``.

        Drives :meth:`GraphRun.counterfactual` (design §5.5, FR-27):
        loads the parent checkpoint at ``step``, mints a fresh
        ``cf-<uuid>`` child run id bound to the cf-derived
        ``graph_hash`` (JCS domain-separation), applies the
        ``mutation`` overlay, and returns the child run handle.

        Rate-limit (design §5.5, locked Decision #6, FR-16, NFR-9):
        per-actor anyio token bucket from
        ``deps["counterfactual_rate_limiter"]`` (lazily seeded by
        :func:`create_app`). Default 10/min/actor; over-limit -> 429
        with ``Retry-After`` header. Bucket state in-memory only --
        process restart resets the per-actor count (brief burst window
        documented in :mod:`harbor.serve.ratelimit`).
        """
        from harbor.graph.run import GraphRun

        rate_limiter: PerActorBucketRegistry | None = app.state.deps.get(
            "counterfactual_rate_limiter"
        )
        if rate_limiter is not None:
            actor = ctx["actor"]
            bucket = await rate_limiter.get_or_create(actor)
            if not await bucket.consume():
                retry_after = await bucket.seconds_until_available()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="counterfactual_rate_limit_exceeded",
                    headers={"Retry-After": str(retry_after)},
                )
        checkpointer: Checkpointer | None = app.state.deps.get("checkpointer")
        if checkpointer is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="checkpointer not wired in deps",
            )
        try:
            cf_run = await GraphRun.counterfactual(
                checkpointer,
                run_id,
                step=body.step,
                mutate=body.mutation,
            )
        except HarborRuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        runs = _resolve_run_registry(app.state.deps)
        runs[cf_run.run_id] = cf_run
        return {
            "run_id": cf_run.run_id,
            "parent_run_id": run_id,
            "status": "pending",
        }

    @app.get("/v1/runs/{run_id}/artifacts")
    async def list_run_artifacts(  # pyright: ignore[reportUnusedFunction]
        run_id: str,
        _ctx: Annotated[AuthContext, Depends(require("artifacts:read"))],
    ) -> list[dict[str, Any]]:
        """List artifacts emitted by ``run_id`` (design §5.1, §10.4).

        Backed by :meth:`ArtifactStore.list` on
        ``deps["artifact_store"]``. A run that wrote no artifacts
        returns ``[]`` (per :class:`FilesystemArtifactStore.list`
        contract). Missing artifact store -> 503.
        """
        store: ArtifactStore | None = app.state.deps.get("artifact_store")
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="artifact_store not wired in deps",
            )
        refs: list[ArtifactRef] = await store.list(run_id)
        return [r.model_dump(mode="json") for r in refs]

    @app.get("/v1/artifacts/{artifact_id}")
    async def get_artifact(  # pyright: ignore[reportUnusedFunction]
        artifact_id: str,
        _ctx: Annotated[AuthContext, Depends(require("artifacts:read"))],
    ) -> Response:
        """Fetch raw artifact bytes (design §5.1, §10.4).

        Returns ``application/octet-stream`` by default; when the
        artifact store can resolve the sidecar metadata (POC: walks
        ``store.list(...)`` for a matching ``artifact_id`` to recover
        the persisted ``content_type``) the response Content-Type
        echoes the sidecar value. Missing artifact -> 404.
        """
        store: ArtifactStore | None = app.state.deps.get("artifact_store")
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="artifact_store not wired in deps",
            )
        try:
            content = await store.get(artifact_id)
        except ArtifactNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"artifact {artifact_id!r} not found",
            ) from exc
        # Best-effort content_type recovery from sidecar metadata. The
        # ``ArtifactStore`` Protocol's ``get`` returns bytes only; the
        # sidecar is reachable via ``list(run_id)`` but we don't know
        # the run_id here. Phase 3 polish: extend the Protocol with a
        # ``stat(artifact_id) -> ArtifactRef`` accessor. For POC we
        # default to octet-stream; integrators may extend the route by
        # writing a thin wrapper.
        return Response(
            content=content,
            media_type="application/octet-stream",
        )

    @app.get("/v1/graphs", response_model=list[_GraphSummary])
    async def list_graphs(  # pyright: ignore[reportUnusedFunction]
        _ctx: Annotated[AuthContext, Depends(require("runs:read"))],
    ) -> list[_GraphSummary]:
        """List registered graphs (design §5.1).

        POC: reads ``deps["graphs"]`` -- a ``dict[str, Graph]`` keyed
        by ``graph_id`` populated by the lifespan factory at startup.
        Phase 3 polish wires the real graph registry (the
        :mod:`harbor.registry`-backed graph table) once the registry
        Protocol's graph subspace lands.
        """
        graphs: dict[str, Any] = app.state.deps.get("graphs") or {}
        out: list[_GraphSummary] = []
        for graph_id, graph in graphs.items():
            graph_hash = getattr(graph, "graph_hash", "")
            out.append(
                _GraphSummary(graph_id=graph_id, graph_hash=str(graph_hash)),
            )
        return out

    @app.get("/v1/registry/{kind}")
    async def list_registry(  # pyright: ignore[reportUnusedFunction]
        kind: str,
        _ctx: Annotated[AuthContext, Depends(require("runs:read"))],
    ) -> list[dict[str, Any]]:
        """List plugin-discovered registry entries for ``kind``.

        ``kind`` is one of ``tools``, ``skills``, ``stores``. Backend:
        ``deps["registry"]`` -- the :mod:`harbor.registry` instance the
        lifespan factory populates from the pluggy-loaded plugin
        manifests. Returns the spec records as JSON dicts. Unknown
        ``kind`` -> 404.

        POC: when ``deps["registry"]`` is absent (ad-hoc
        ``create_app(profile, deps={})`` calls without lifespan
        wiring) the route returns an empty list rather than 503 --
        consistent with the permissive POC default elsewhere in this
        module. Cleared deployments may flip this to 503 in Phase 3.
        """
        if kind not in ("tools", "skills", "stores"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown registry kind: {kind!r}",
            )
        registry_obj: Any = app.state.deps.get("registry")
        if registry_obj is None:
            return []
        # Dispatcher: each registry kind has its own list method on a
        # different registry object. The lifespan factory packs them
        # under a single key as a dict (``{"tools": ToolRegistry,
        # "stores": StoreRegistry}``) so this route doesn't need to
        # know which class lives where. Skills are exposed via
        # ``ToolRegistry.list_skills()`` (Phase-1 stub returns ``[]``).
        registry_dict: dict[str, Any] = (
            cast("dict[str, Any]", registry_obj) if isinstance(registry_obj, dict) else {}
        )
        items: list[Any] = []
        if kind == "tools":
            tool_reg: Any = registry_dict.get("tools")
            if tool_reg is not None:
                raw: list[Any] = list(tool_reg.list_tools())
                items = [getattr(t, "spec", t) for t in raw]
        elif kind == "skills":
            tool_reg = registry_dict.get("tools")
            if tool_reg is not None:
                items = list(tool_reg.list_skills())
        else:  # kind == "stores"
            store_reg: Any = registry_dict.get("stores")
            if store_reg is not None:
                items = list(store_reg.list_stores())
        out: list[dict[str, Any]] = []
        for spec in items:
            if hasattr(spec, "model_dump"):
                out.append(spec.model_dump(mode="json"))
            else:
                out.append(dict(spec))
        return out

    return app


def _summarize(run: GraphRun) -> RunSummary:
    """Synthesize a :class:`RunSummary` from a live :class:`GraphRun` handle.

    Mirrors the status-lattice fold used by
    :func:`harbor.serve.lifecycle._build_run_summary` and
    :func:`harbor.serve.respond._build_run_summary` (task 1.22 / 1.23):
    the wider :class:`GraphRun.state` Literal
    (``pending|running|paused|awaiting-input|done|cancelled|error|failed``)
    is folded onto the narrower :class:`Checkpointer.RunSummary.status`
    Literal (``running|done|failed|paused``). Phase 2 widens the
    :class:`RunSummary` Literal (task 1.2 backfill) and reads the
    canonical row from the Checkpointer instead.
    """
    from datetime import UTC, datetime

    from harbor.checkpoint.protocol import RunSummary

    now = datetime.now(UTC)
    status_mapped: str
    if run.state == "cancelled":
        status_mapped = "failed"
    elif run.state == "awaiting-input":
        status_mapped = "paused"
    elif run.state in ("running", "done", "failed", "paused"):
        status_mapped = run.state
    else:
        status_mapped = "failed"
    return RunSummary(
        run_id=run.run_id,
        graph_hash=run.graph.graph_hash,
        started_at=now,
        last_step_at=now,
        status=status_mapped,  # pyright: ignore[reportArgumentType]
        parent_run_id=run.parent_run_id,
    )
