# SPDX-License-Identifier: Apache-2.0
"""E3 contract tests for cve_rem broker-intent payloads.

Each typed intent must:

  - Have a stable ``intent_name`` literal matching the canonical
    ``cve_rem.<verb>`` namespace.
  - Project to a ``Broker.arequest`` kwargs dict (agent_id / intent /
    context) without losing fields.
  - Round-trip through ``model_dump`` → JSON → ``model_validate``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from demos.cve_remediation.graph.intents import (
    AuditAnchorIntent,
    BrokerIntent,
    CargoNetWritebackIntent,
    CorrelateAssetsIntent,
    CreateChangeRequestIntent,
    DriftWatchSpawnIntent,
    EpssKevRefreshIntent,
    LabLeakReaperIntent,
    PublishDocPlusIntent,
    RestartBatchIntent,
    SpawnChildRunIntent,
    TierReEvalSpawnIntent,
    broker_call_args,
    build_intent_context,
)

INTENT_FACTORIES = [
    lambda: CorrelateAssetsIntent(cve_id="CVE-2026-1"),
    lambda: CreateChangeRequestIntent(
        cve_id="CVE-2026-1",
        plan_hash="abc",
        affected_assets=["host-a"],
        code_runtime="ansible",
        ssvc_tier="act_auto",
    ),
    lambda: DriftWatchSpawnIntent(cve_id="CVE-2026-1", parent_run_id="run-1"),
    lambda: PublishDocPlusIntent(docx_artifact_ref="docx://x"),
    lambda: CargoNetWritebackIntent(retro_id="retro-1", lab_scenario_id="lab-1", success=True),
    lambda: AuditAnchorIntent(chain_head_sha256="abc", partition_date="2026-05-04"),
    lambda: LabLeakReaperIntent(),
    lambda: TierReEvalSpawnIntent(parent_run_id="run-1"),
    lambda: EpssKevRefreshIntent(snapshot_date="2026-05-04"),
    lambda: RestartBatchIntent(batch_index=1, artifact_id="a-1", worker_replication_group="g-1"),
    lambda: SpawnChildRunIntent(target_graph_id="graph:cve-rem-drift-watch", parent_run_id="r-1"),
]


@pytest.mark.parametrize("factory", INTENT_FACTORIES)
def test_intent_constructs(factory) -> None:
    intent = factory()
    assert intent.intent_name.startswith("cve_rem.")


@pytest.mark.parametrize("factory", INTENT_FACTORIES)
def test_intent_context_excludes_intent_name(factory) -> None:
    intent = factory()
    ctx = build_intent_context(intent)
    assert "intent_name" not in ctx


@pytest.mark.parametrize("factory", INTENT_FACTORIES)
def test_broker_call_args_shape(factory) -> None:
    intent = factory()
    args = broker_call_args(intent)
    assert set(args) == {"agent_id", "intent", "context"}
    assert args["agent_id"] == "cve-rem-pipeline"
    assert args["intent"] == intent.intent_name
    assert isinstance(args["context"], dict)


@pytest.mark.parametrize("factory", INTENT_FACTORIES)
def test_intent_json_roundtrip(factory) -> None:
    intent = factory()
    payload = json.loads(intent.model_dump_json())
    cls = type(intent)
    rehydrated = cls.model_validate(payload)
    assert rehydrated == intent


def test_correlate_assets_requires_cve_id() -> None:
    with pytest.raises(ValidationError):
        CorrelateAssetsIntent()  # type: ignore[call-arg]


def test_create_change_request_requires_full_payload() -> None:
    with pytest.raises(ValidationError):
        CreateChangeRequestIntent(cve_id="CVE-X")  # type: ignore[call-arg]


def test_restart_batch_index_in_range() -> None:
    """Literal[1,2,3] enforces 3 batches max."""
    with pytest.raises(ValidationError):
        RestartBatchIntent(  # type: ignore[arg-type]
            batch_index=4,
            artifact_id="a",
            worker_replication_group="g",
        )


def test_audit_anchor_jws_default_true() -> None:
    intent = AuditAnchorIntent(chain_head_sha256="abc", partition_date="2026-05-04")
    assert intent.submit_to_jws_chain is True


def test_intent_namespace_count() -> None:
    """Total broker-intent surface — locks demo coverage."""
    # 11 typed intents map directly to the broker call sites in the IRs.
    expected_intents = {
        "cve_rem.correlate_assets",
        "cve_rem.create_change_request",
        "cve_rem.drift_watch_spawn",
        "cve_rem.publish_docplus",
        "cve_rem.cargonet_writeback",
        "cve_rem.audit_anchor",
        "cve_rem.list_active_labs",
        "cve_rem.tier_re_eval_spawn",
        "cve_rem.refresh_epss_kev",
        "cve_rem.restart_batch",
        "cve_rem.spawn_child_run",
    }
    actual_intents = {factory().intent_name for factory in INTENT_FACTORIES}
    assert actual_intents == expected_intents
