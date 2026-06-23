# SPDX-License-Identifier: Apache-2.0
"""Pydantic request/response models for the Stargraph serve API.

Extracted verbatim from :mod:`stargraph.serve.api` so the app-factory module
stays focused on route wiring. All models are re-exported from
:mod:`stargraph.serve.api`; the route handlers reference them there.

Design refs: §5.1 (endpoint table / response shapes), §5.5 (counterfactual),
§6.5 (pagination).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- runtime use by pydantic field
from typing import Any

from pydantic import BaseModel, Field

from stargraph.replay.counterfactual import (
    CounterfactualMutation,  # noqa: TC001 -- pydantic resolves at runtime in the request model
)

# Listed so the (intentionally private, underscore-prefixed) models are an
# explicit re-export surface: :mod:`stargraph.serve.api` and the test suite
# import them from here / from ``serve.api``. Naming them in ``__all__`` marks
# the cross-module import as deliberate for pyright strict (reportPrivateUsage).
__all__ = [
    "_CounterfactualRequest",
    "_GraphSummary",
    "_RespondRequest",
    "_ResumeRequest",
    "_RunSummaryItem",
    "_RunsPage",
    "_StartRunRequest",
    "_StartRunResponse",
]


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
    :class:`stargraph.serve.history.RunRecord` but exposed as the
    canonical ``RunSummary`` API surface (design §5.1 ``GET /v1/runs``
    returns ``Page[RunSummary]``). Distinct from
    :class:`stargraph.checkpoint.protocol.RunSummary` (which is the
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
    #: Terminal failure diagnostics (#68), mirrored straight off the
    #: ``runs_history`` row. ``error_class`` is a coarse discriminator
    #: (``"interrupt_timeout"`` or the node exception type name),
    #: ``error_cause`` a short message. ``None`` on the success path.
    error_class: str | None = None
    error_cause: str | None = None


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
