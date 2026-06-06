# SPDX-License-Identifier: Apache-2.0
"""Streaming event vocabulary -- 16-type discriminated union.

References: FR-14, FR-38, FR-79, FR-83, FR-87, FR-93; design §3.7.1, §4.3.

Every engine event ships the standard envelope ``{type, run_id, step,
branch_id, ts, payload}`` plus zero-or-more typed fields specific to the
event variant. The :data:`Event` alias is a Pydantic v2 discriminated
union over the ``type`` Literal field -- callers may
``Event.model_validate(d)`` (via a TypeAdapter) and Pydantic dispatches
to the correct subclass without reflection.

Subclasses inherit ``extra='forbid'`` from :class:`stargraph.ir.IRBase`, so
typos in event payloads fail loudly at validation time (FR-6, AC-9.1).
The ``payload: dict[str, Any]`` field on :class:`EventBase` defaults
empty and is unused by the typed variants; it exists as a convenience
escape hatch for ad-hoc events that do not yet have a dedicated
subclass.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- pydantic resolves at runtime
from typing import Annotated, Any, Literal

from pydantic import Field

from stargraph.ir import IRBase

__all__ = [
    "ArtifactWrittenEvent",
    "BosunAuditEvent",
    "BranchCancelledEvent",
    "BranchCompletedEvent",
    "BranchStartedEvent",
    "CheckpointEvent",
    "ErrorEvent",
    "Event",
    "EventBase",
    "InterruptTimeoutEvent",
    "ResultEvent",
    "RunCancelledEvent",
    "RunPausedEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "TransitionEvent",
    "WaitingForInputEvent",
]


class EventBase(IRBase):
    """Common envelope for every Stargraph runtime event (design §3.7.1).

    Each concrete subclass independently declares a ``type: Literal[...]``
    discriminator field (mirroring how :data:`stargraph.ir.Action` variants
    each declare their own ``kind`` Literal). The base intentionally
    omits ``type`` to keep pyright's mutable-field invariance check
    happy across the discriminated union.
    """

    run_id: str
    step: int
    branch_id: str | None = None
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])


class TokenEvent(EventBase):
    """LLM token emission (FR-14)."""

    type: Literal["token"] = "token"
    model: str
    token: str
    index: int


class ToolCallEvent(EventBase):
    """Tool invocation request (FR-14)."""

    type: Literal["tool_call"] = "tool_call"
    tool_name: str
    namespace: str
    args: dict[str, Any]
    call_id: str


class ToolResultEvent(EventBase):
    """Tool invocation result (FR-14)."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class TransitionEvent(EventBase):
    """Rule fired -> node transition (FR-14)."""

    type: Literal["transition"] = "transition"
    from_node: str
    to_node: str
    rule_id: str
    reason: str


class CheckpointEvent(EventBase):
    """Checkpoint persisted (FR-14)."""

    type: Literal["checkpoint"] = "checkpoint"
    checkpoint_id: str


class ErrorEvent(EventBase):
    """Recoverable or fatal error during execution (FR-14)."""

    type: Literal["error"] = "error"
    scope: Literal["node", "rule", "tool", "runtime", "checkpoint"]
    message: str
    recoverable: bool


class ResultEvent(EventBase):
    """Terminal run summary (FR-14)."""

    type: Literal["result"] = "result"
    status: Literal["done", "failed", "paused"]
    final_state: dict[str, Any]
    run_duration_ms: int


class BranchStartedEvent(EventBase):
    """Parallel/Join branch fork (FR-14).

    Branch events always carry a non-None ``branch_id`` at runtime
    (callers MUST populate it); the base type stays ``str | None`` to
    keep the override variance pyright-clean.
    """

    type: Literal["branch_started"] = "branch_started"
    target: str
    strategy: str


class BranchCompletedEvent(EventBase):
    """Parallel/Join branch finished successfully (FR-14)."""

    type: Literal["branch_completed"] = "branch_completed"
    result: dict[str, Any]


class BranchCancelledEvent(EventBase):
    """Parallel/Join branch cancelled (race/quorum loser, timeout, etc.) (FR-14)."""

    type: Literal["branch_cancelled"] = "branch_cancelled"
    reason: str


class RunPausedEvent(EventBase):
    """Run paused via cooperative pause boundary (design §4.3, FR-79)."""

    type: Literal["run_paused"] = "run_paused"
    actor: str


class RunCancelledEvent(EventBase):
    """Run cancelled cooperatively (design §4.3, FR-83)."""

    type: Literal["run_cancelled"] = "run_cancelled"
    actor: str
    reason: Literal["user", "timeout", "shutdown"]


class WaitingForInputEvent(EventBase):
    """HITL pause -- engine awaiting `respond` (design §4.3, FR-87, AC-14.3).

    ``interrupt_payload`` is the IR-supplied free-form payload (analyst
    context); ``requested_capability`` is the optional capability the
    responder must hold to satisfy the gate.
    """

    type: Literal["waiting_for_input"] = "waiting_for_input"
    prompt: str
    interrupt_payload: dict[str, Any]
    requested_capability: str | None = None


class InterruptTimeoutEvent(EventBase):
    """HITL interrupt timed out before `respond` (design §4.3, FR-87)."""

    type: Literal["interrupt_timeout"] = "interrupt_timeout"
    on_timeout: str  # "halt" or "goto:<node_id>"


class ArtifactWrittenEvent(EventBase):
    """Artifact persisted via :class:`WriteArtifactNode` (design §4.3, FR-93, AC-15.4).

    ``artifact_ref`` carries the ``ArtifactRef`` payload (BLAKE3
    content-addressed; type lives in :mod:`stargraph.artifacts.base` once
    landed). ``provenance`` is the originating run/step/actor lineage
    bundle and MUST carry the ProvenanceBundle tuple
    ``(origin, source, run_id, step, confidence, timestamp)`` so the
    JSONL lineage audit (FR-55, AC-11.2) treats system-emitted facts
    with the same chain-of-custody contract as user-asserted facts.
    Both fields use ``dict[str, Any]`` until the artifacts module lands;
    subsequent tasks may promote them to typed models.
    """

    type: Literal["artifact_written"] = "artifact_written"
    artifact_ref: dict[str, Any]
    provenance: dict[str, Any]


class BosunAuditEvent(EventBase):
    """`stargraph.bosun.audit` pack fact promoted to typed event (design §4.3, FR-38).

    Emitted by :class:`stargraph.fathom.FathomAdapter` when a CLIPS rule
    asserts a ``bosun.audit`` fact. Flows through the single
    :class:`JSONLAuditSink` (Resolved Decision #5) -- no parallel sink.

    ``provenance`` carries the ProvenanceBundle-shaped tuple
    ``(origin, source, run_id, step, confidence, timestamp)`` so the
    JSONL audit lineage (FR-55, AC-11.2) treats every audited fact --
    user-asserted or system-emitted -- with the same chain-of-custody
    contract. For system-emitted audit events the canonical shape is
    ``origin="system"``, ``source=<emitter-module>``, ``confidence=1.0``,
    ``timestamp=<wall-clock UTC>``. The dict shape (rather than typed
    sub-model) mirrors :class:`ArtifactWrittenEvent` until the unified
    ``Provenance`` model in :mod:`stargraph.runtime.tool_exec` is promoted
    to a public symbol.
    """

    type: Literal["bosun_audit"] = "bosun_audit"
    pack_id: str
    pack_version: str
    fact: dict[str, Any]
    provenance: dict[str, Any]


Event = Annotated[
    TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | TransitionEvent
    | CheckpointEvent
    | ErrorEvent
    | ResultEvent
    | BranchStartedEvent
    | BranchCompletedEvent
    | BranchCancelledEvent
    | RunPausedEvent
    | RunCancelledEvent
    | WaitingForInputEvent
    | InterruptTimeoutEvent
    | ArtifactWrittenEvent
    | BosunAuditEvent,
    Field(discriminator="type"),
]
"""Discriminated union over the 16 typed events (FR-14, design §3.7.1, §4.3).

Pydantic dispatches on the ``type`` Literal -- callers should validate via
``pydantic.TypeAdapter(Event).validate_python(d)`` (the alias itself is
not a class and cannot be called directly).
"""
