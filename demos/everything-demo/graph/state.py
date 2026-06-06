# SPDX-License-Identifier: Apache-2.0
"""everything-demo run state.

Single Pydantic state schema covering every field every node in
``stargraph.yaml`` reads or writes. Intentionally flat — Stargraph's
field-merge registry (FR-11) keys on top-level attribute names, so all
node outputs land at a single dotless key here.

Pull this in via ``state_class:`` in stargraph.yaml:

    state_class: "demos.everything_demo.graph.state:RunState"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TicketHit(BaseModel):
    """One row of retrieved knowledge-base context."""

    id: str
    score: float
    text: str
    source: Literal["vector", "graph", "doc"]


class HitlResponse(BaseModel):
    """Shape of the analyst response patched in by ``GraphRun.respond``."""

    decision: Literal["approve", "reject", "escalate"]
    actor: str
    note: str = ""
    at: datetime


class RunState(BaseModel):
    """everything-demo run state (covers every node's read/write fields)."""

    # provenance
    run_id: str
    parent_run_id: str | None = None
    trigger_kind: Literal["manual", "cron", "webhook"] = "manual"

    # intake (set by start sentinel from trigger payload)
    ticket_id: str
    ticket_text: str
    ticket_source: Literal["email", "chat", "form", "api"]

    # dspy: intent classifier
    intent: str = ""
    intent_confidence: float = 0.0

    # retrieval (RetrievalNode)
    query: str = ""
    retrieved_hits: list[TicketHit] = Field(default_factory=list)

    # ml (MLNode sklearn classifier)
    risk_score: float = 0.0
    risk_class: Literal["low", "medium", "high", ""] = ""

    # tool call (lookup_history @tool)
    history_count: int = 0
    history_summary: str = ""

    # broker (BrokerNode → Nautilus → SOC SoR)
    compliance_status: Literal["clean", "flagged", "pending", ""] = ""
    broker_request_id: str = ""

    # subgraph (enrichment subgraph signal)
    enrichment_done: bool = False

    # hitl (InterruptNode + respond)
    response: HitlResponse | None = None

    # memory write (MemoryWriteNode)
    episode_id: str = ""
    memory_written: bool = False

    # artifact (WriteArtifactNode)
    artifact_id: str = ""

    # mcp tool (notify_user)
    notify_status: Literal["sent", "skipped", ""] = ""

    # final summary (DSPyNode)
    resolution_summary: str = ""

    # transient flags consumed by routing rules
    broker_attempt: int = 0
    validation_passed: bool = False
    fact_pinned: bool = False
    fact_retracted: bool = False
    parallel_search_done: bool = False
    skip_enrichment: bool = False

    # provenance envelopes patched by tool-shaped nodes
    __stargraph_provenance__: dict[str, Any] = Field(default_factory=dict)


__all__ = ["HitlResponse", "RunState", "TicketHit"]
