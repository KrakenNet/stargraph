# SPDX-License-Identifier: Apache-2.0
"""Typed Nautilus broker-intent payloads for cve_remediation (E3).

Every broker call in the demo goes through ``nautilus.broker_request@1``
(canonical broker pattern, FR-44). The Nautilus :meth:`Broker.arequest`
signature is ``(agent_id, intent: str, context: dict[str, Any], ...)``.

This module defines:

  - ``BrokerIntent``: discriminated-union of every intent the demo emits.
  - One :class:`pydantic.BaseModel` per broker call site, each carrying
    the typed payload + the canonical ``intent_name`` string.
  - :func:`build_intent_context`: project a typed intent to the
    ``context`` dict ``Broker.arequest`` consumes.

E3 boundary: the typed envelope + builder land here; live dispatch
to ``Broker.arequest`` is wired by ``BrokerCallNode`` (a Phase E
follow-up subclassing :class:`harbor.nodes.nautilus.broker_node.BrokerNode`).
This separation keeps payload validation testable offline (no broker
singleton, no async loop) while still enforcing the wire-format
contract at construction time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Phase 2: asset correlation
# ---------------------------------------------------------------------------


class CorrelateAssetsIntent(BaseModel):
    """Phase-2 intent: cve_id → affected assets via CMDB + Nautobot."""

    intent_name: Literal["cve_rem.correlate_assets"] = "cve_rem.correlate_assets"
    cve_id: str
    affected_products: list[str] = Field(default_factory=list)
    affected_versions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 4: change request + execute
# ---------------------------------------------------------------------------


class CreateChangeRequestIntent(BaseModel):
    """Phase-4 intent: open ServiceNow CR for the remediation bundle."""

    intent_name: Literal["cve_rem.create_change_request"] = "cve_rem.create_change_request"
    cve_id: str
    plan_hash: str
    affected_assets: list[str]
    code_runtime: str
    ssvc_tier: str
    cr_template: str = "STANDARD"


class DriftWatchSpawnIntent(BaseModel):
    """Phase-4 intent: spawn a ``cve-rem-drift-watch`` child run."""

    intent_name: Literal["cve_rem.drift_watch_spawn"] = "cve_rem.drift_watch_spawn"
    cve_id: str
    parent_run_id: str
    watch_window_hours: int = 48


# ---------------------------------------------------------------------------
# Phase 5: retro fan-out
# ---------------------------------------------------------------------------


class PublishDocPlusIntent(BaseModel):
    intent_name: Literal["cve_rem.publish_docplus"] = "cve_rem.publish_docplus"
    docx_artifact_ref: str
    audience: Literal["internal", "customer", "regulator"] = "internal"


class CargoNetWritebackIntent(BaseModel):
    intent_name: Literal["cve_rem.cargonet_writeback"] = "cve_rem.cargonet_writeback"
    retro_id: str
    lab_scenario_id: str
    success: bool


# ---------------------------------------------------------------------------
# Triggered: audit anchor + lab reaper + tier re-eval + rolling restart
# ---------------------------------------------------------------------------


class AuditAnchorIntent(BaseModel):
    intent_name: Literal["cve_rem.audit_anchor"] = "cve_rem.audit_anchor"
    chain_head_sha256: str
    partition_date: str
    submit_to_jws_chain: bool = True


class LabLeakReaperIntent(BaseModel):
    intent_name: Literal["cve_rem.list_active_labs"] = "cve_rem.list_active_labs"
    ttl_seconds: int = 86_400  # default 24h


class TierReEvalSpawnIntent(BaseModel):
    intent_name: Literal["cve_rem.tier_re_eval_spawn"] = "cve_rem.tier_re_eval_spawn"
    cve_asset_pairs: list[tuple[str, str]] = Field(default_factory=list)
    parent_run_id: str = ""


class EpssKevRefreshIntent(BaseModel):
    intent_name: Literal["cve_rem.refresh_epss_kev"] = "cve_rem.refresh_epss_kev"
    snapshot_date: str
    cve_ids: list[str] = Field(default_factory=list)


class RestartBatchIntent(BaseModel):
    intent_name: Literal["cve_rem.restart_batch"] = "cve_rem.restart_batch"
    batch_index: Literal[1, 2, 3]
    artifact_id: str
    worker_replication_group: str


# ---------------------------------------------------------------------------
# Cross-graph spawn (used by drift_watch + tier_re_eval)
# ---------------------------------------------------------------------------


class SpawnChildRunIntent(BaseModel):
    intent_name: Literal["cve_rem.spawn_child_run"] = "cve_rem.spawn_child_run"
    target_graph_id: str
    parent_run_id: str
    initial_state: dict[str, Any] = Field(default_factory=dict)
    spawned_at: datetime = Field(default_factory=lambda: datetime.now())


# ---------------------------------------------------------------------------
# Discriminated union — full intent surface
# ---------------------------------------------------------------------------

BrokerIntent = (
    CorrelateAssetsIntent
    | CreateChangeRequestIntent
    | DriftWatchSpawnIntent
    | PublishDocPlusIntent
    | CargoNetWritebackIntent
    | AuditAnchorIntent
    | LabLeakReaperIntent
    | TierReEvalSpawnIntent
    | EpssKevRefreshIntent
    | RestartBatchIntent
    | SpawnChildRunIntent
)


_AGENT_ID = "cve-rem-pipeline"


def build_intent_context(intent: BrokerIntent) -> dict[str, Any]:
    """Project a typed intent → broker-request context dict.

    The context dict is what ``Broker.arequest(agent_id, intent_name,
    context=...)`` consumes. We exclude the ``intent_name`` field
    itself since that goes in the ``intent`` positional kwarg.
    """
    return intent.model_dump(mode="json", exclude={"intent_name"})


def broker_call_args(intent: BrokerIntent) -> dict[str, Any]:
    """Return the kwargs dict for :meth:`nautilus.Broker.arequest`.

    Phase-E broker dispatch: ``await broker.arequest(**broker_call_args(intent))``.
    Keeps the demo honest about the wire shape even though the demo
    runs offline (no live broker singleton).
    """
    return {
        "agent_id": _AGENT_ID,
        "intent": intent.intent_name,
        "context": build_intent_context(intent),
    }


__all__ = [
    "AuditAnchorIntent",
    "BrokerIntent",
    "CargoNetWritebackIntent",
    "CorrelateAssetsIntent",
    "CreateChangeRequestIntent",
    "DriftWatchSpawnIntent",
    "EpssKevRefreshIntent",
    "LabLeakReaperIntent",
    "PublishDocPlusIntent",
    "RestartBatchIntent",
    "SpawnChildRunIntent",
    "TierReEvalSpawnIntent",
    "broker_call_args",
    "build_intent_context",
]
