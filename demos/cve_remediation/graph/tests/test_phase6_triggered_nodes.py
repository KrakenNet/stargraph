# SPDX-License-Identifier: Apache-2.0
"""Tests for Phase 6 + 5 triggered-graph real node bodies (S3.6)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from demos.cve_remediation.graph.real_nodes import (
    AnchorViaNautilusNode,
    ClassifyDriftNode,
    CollectDriftEventsNode,
    EmitAnchorReceiptNode,
    EmitDriftWindowSummaryNode,
    EmitReEvalSummaryNode,
    EmitReaperSummaryNode,
    EmitRedactedCorpusNode,
    EmitRestartSummaryNode,
    EmitRollbackRecordNode,
    FireHaltNewNode,
    GepaCompileCriticNode,
    GepaCompilePlannerNode,
    HealthGateBatch1Node,
    HealthGateBatch2Node,
    HealthGateBatch3Node,
    ListActiveLabsNode,
    ListWorkerBatchesNode,
    NoopCleanNode,
    PageOncallNode,
    PageSecurityOncallNode,
    PullHoldoutRetrosNode,
    ReEvaluateSsvcNode,
    ReadChainHeadNode,
    ReapExpiredNode,
    RecordFailureNode,
    RedactionTransformNode,
    RefreshEpssKevNode,
    RejectArtifactNode,
    RestartBatch1Node,
    RestartBatch2Node,
    RestartBatch3Node,
    RollbackPointerNode,
    ScanTrackedDeferredNode,
    SelectArtifactNode,
    ShamirCeremonyNode,
    ShipToPromptsDirNode,
    SignalRollingRestartNode,
    SnapshotCurrentPointerNode,
    SpawnChildRunNode,
    SpawnMainPipelineRunsNode,
)
from demos.cve_remediation.graph.state import (
    AuditAnchorState,
    CveRemState,
    DriftWatchState,
    LabLeakReaperState,
    RollingRestartState,
    TierReEvalState,
)


def _ctx() -> object:
    return object()


@pytest.fixture
def isolated_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HARBOR_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    import importlib

    import demos.cve_remediation.graph.real_nodes as rn_mod

    importlib.reload(rn_mod)
    return tmp_path / "artifacts"


# ---------------------------------------------------------------------------
# Phase 6
# ---------------------------------------------------------------------------


def test_pull_holdout_retros_default() -> None:
    out = asyncio.run(PullHoldoutRetrosNode().execute(CveRemState(), _ctx()))
    assert out["holdout_retro_count"] == 50


def test_pull_holdout_retros_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVE_REM_HOLDOUT_COUNT", "200")
    out = asyncio.run(PullHoldoutRetrosNode().execute(CveRemState(), _ctx()))
    assert out["holdout_retro_count"] == 200


def test_redaction_transform_deterministic() -> None:
    state = CveRemState(holdout_retro_count=50, run_id="r-1")
    a = asyncio.run(RedactionTransformNode().execute(state, _ctx()))
    b = asyncio.run(RedactionTransformNode().execute(state, _ctx()))
    assert a == b
    assert len(a["redacted_corpus_hash"]) == 16


def test_emit_redacted_corpus(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitRedactedCorpusNode

    state = CveRemState(redacted_corpus_hash="abc", holdout_retro_count=50)
    out = asyncio.run(EmitRedactedCorpusNode().execute(state, _ctx()))
    assert out["redacted_corpus_artifact_ref"].startswith("file://")


def test_gepa_compile_planner_seeds_components() -> None:
    state = CveRemState(redacted_corpus_hash="abc")
    out = asyncio.run(GepaCompilePlannerNode().execute(state, _ctx()))
    assert len(out["candidate_artifact_hash"]) == 16
    components = out["gepa_components"]
    assert set(components.keys()) == {
        "validation",
        "sandbox",
        "cr_approved",
        "no_drift_7d",
        "no_rollback_30d",
    }
    for v in components.values():
        assert 7000 <= v <= 9550


def test_gepa_compile_critic() -> None:
    state = CveRemState(candidate_artifact_hash="abc")
    out = asyncio.run(GepaCompileCriticNode().execute(state, _ctx()))
    assert len(out["current_artifact_hash"]) == 16


def test_reject_artifact_halt_reason() -> None:
    out = asyncio.run(RejectArtifactNode().execute(CveRemState(), _ctx()))
    assert "below epsilon margin" in out["halt_reason"]


def test_shamir_ceremony_default_reached() -> None:
    out = asyncio.run(ShamirCeremonyNode().execute(CveRemState(), _ctx()))
    assert out["shamir_quorum"] == "reached"


def test_shamir_ceremony_env_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVE_REM_SHAMIR_QUORUM", "not_reached")
    out = asyncio.run(ShamirCeremonyNode().execute(CveRemState(), _ctx()))
    assert out["shamir_quorum"] == "not_reached"


def test_ship_to_prompts_dir_emits_id() -> None:
    state = CveRemState(candidate_artifact_hash="abc")
    out = asyncio.run(ShipToPromptsDirNode().execute(state, _ctx()))
    assert len(out["ship_audit_id"]) == 16


def test_signal_rolling_restart_envelope() -> None:
    state = CveRemState(candidate_artifact_hash="abc", run_id="parent-1")
    out = asyncio.run(SignalRollingRestartNode().execute(state, _ctx()))
    assert out["last_broker_intent"] == "cve_rem.spawn_child_run"
    payload = out["broker_request_envelope"]["context"]
    assert payload["target_graph_id"] == "graph:cve-rem-rolling-restart"
    assert payload["initial_state"]["artifact_id"] == "abc"


# ---------------------------------------------------------------------------
# Audit anchor
# ---------------------------------------------------------------------------


def test_read_chain_head() -> None:
    out = asyncio.run(ReadChainHeadNode().execute(AuditAnchorState(), _ctx()))
    assert len(out["chain_head_sha256"]) == 64
    assert len(out["partition_date"]) == 10  # YYYY-MM-DD


def test_anchor_via_nautilus_default_ok() -> None:
    state = AuditAnchorState(chain_head_sha256="x", partition_date="2026-05-04")
    out = asyncio.run(AnchorViaNautilusNode().execute(state, _ctx()))
    assert out["anchor_status"] == "ok"
    assert out["last_broker_intent"] == "cve_rem.audit_anchor"


def test_anchor_via_nautilus_env_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVE_REM_AUDIT_ANCHOR_STATUS", "failed")
    out = asyncio.run(AnchorViaNautilusNode().execute(AuditAnchorState(), _ctx()))
    assert out["anchor_status"] == "failed"


def test_emit_anchor_receipt(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitAnchorReceiptNode

    state = AuditAnchorState(chain_head_sha256="x", anchor_status="ok")
    out = asyncio.run(EmitAnchorReceiptNode().execute(state, _ctx()))
    assert out["receipt_artifact_ref"].startswith("file://")


def test_record_failure_increments() -> None:
    state = AuditAnchorState(sustained_failure_hours=23)
    out = asyncio.run(RecordFailureNode().execute(state, _ctx()))
    assert out["sustained_failure_hours"] == 24


def test_page_security_oncall_halt() -> None:
    out = asyncio.run(PageSecurityOncallNode().execute(AuditAnchorState(), _ctx()))
    assert "24h" in out["halt_reason"]


def test_fire_halt_new_halt() -> None:
    out = asyncio.run(FireHaltNewNode().execute(AuditAnchorState(), _ctx()))
    assert "72h" in out["halt_reason"]


# ---------------------------------------------------------------------------
# Drift watch
# ---------------------------------------------------------------------------


def test_collect_drift_events_default_clean() -> None:
    out = asyncio.run(CollectDriftEventsNode().execute(DriftWatchState(), _ctx()))
    assert out == {"drift_detected": False}


def test_collect_drift_events_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVE_REM_DRIFT_DETECTED", "true")
    out = asyncio.run(CollectDriftEventsNode().execute(DriftWatchState(), _ctx()))
    assert out == {"drift_detected": True}


def test_classify_drift_no_detection() -> None:
    state = DriftWatchState(drift_detected=False)
    out = asyncio.run(ClassifyDriftNode().execute(state, _ctx()))
    assert out["drift_signature_match"] is False


def test_classify_drift_signature_default() -> None:
    state = DriftWatchState(drift_detected=True)
    out = asyncio.run(ClassifyDriftNode().execute(state, _ctx()))
    assert out["drift_signature_match"] is True


def test_spawn_child_run_envelope() -> None:
    state = DriftWatchState(cve_id="CVE-X", run_id="r-1")
    out = asyncio.run(SpawnChildRunNode().execute(state, _ctx()))
    assert out["drift_outcome"] == "spawned"
    assert out["last_broker_intent"] == "cve_rem.spawn_child_run"


def test_page_oncall_paged() -> None:
    out = asyncio.run(PageOncallNode().execute(DriftWatchState(), _ctx()))
    assert out["drift_outcome"] == "paged"


def test_noop_clean() -> None:
    out = asyncio.run(NoopCleanNode().execute(DriftWatchState(), _ctx()))
    assert out["drift_outcome"] == "clean"


def test_emit_drift_window_summary(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitDriftWindowSummaryNode

    state = DriftWatchState(cve_id="CVE-X", drift_outcome="spawned")
    out = asyncio.run(EmitDriftWindowSummaryNode().execute(state, _ctx()))
    assert out["drift_summary_artifact_ref"].startswith("file://")


# ---------------------------------------------------------------------------
# Lab leak reaper
# ---------------------------------------------------------------------------


def test_list_active_labs_envelope() -> None:
    out = asyncio.run(ListActiveLabsNode().execute(LabLeakReaperState(), _ctx()))
    assert out["last_broker_intent"] == "cve_rem.list_active_labs"
    assert out["active_lab_count"] == 5
    assert out["expired_lab_count"] == 2


def test_reap_expired_uses_count() -> None:
    state = LabLeakReaperState(expired_lab_count=7)
    out = asyncio.run(ReapExpiredNode().execute(state, _ctx()))
    assert out["reaped_lab_count"] == 7


def test_emit_reaper_summary(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitReaperSummaryNode

    state = LabLeakReaperState(active_lab_count=5, expired_lab_count=2, reaped_lab_count=2)
    out = asyncio.run(EmitReaperSummaryNode().execute(state, _ctx()))
    assert out["reaper_summary_artifact_ref"].startswith("file://")


# ---------------------------------------------------------------------------
# Tier re-eval
# ---------------------------------------------------------------------------


def test_scan_tracked_deferred_default() -> None:
    out = asyncio.run(ScanTrackedDeferredNode().execute(TierReEvalState(), _ctx()))
    assert out["scanned_pair_count"] == 10


def test_refresh_epss_kev_envelope() -> None:
    out = asyncio.run(RefreshEpssKevNode().execute(TierReEvalState(), _ctx()))
    assert out["last_broker_intent"] == "cve_rem.refresh_epss_kev"


def test_re_evaluate_ssvc_30pct_split() -> None:
    state = TierReEvalState(scanned_pair_count=100)
    out = asyncio.run(ReEvaluateSsvcNode().execute(state, _ctx()))
    assert out["tier_escalations_count"] == 30
    assert out["tier_unchanged_count"] == 70


def test_spawn_main_pipeline_runs_envelope() -> None:
    state = TierReEvalState(tier_escalations_count=3)
    out = asyncio.run(SpawnMainPipelineRunsNode().execute(state, _ctx()))
    assert len(out["spawned_run_ids"]) == 3
    assert out["last_broker_intent"] == "cve_rem.tier_re_eval_spawn"


def test_emit_re_eval_summary(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitReEvalSummaryNode

    state = TierReEvalState(scanned_pair_count=10, tier_escalations_count=3)
    out = asyncio.run(EmitReEvalSummaryNode().execute(state, _ctx()))
    assert out["summary_artifact_ref"].startswith("file://")


# ---------------------------------------------------------------------------
# Rolling restart
# ---------------------------------------------------------------------------


def test_select_artifact_default() -> None:
    out = asyncio.run(SelectArtifactNode().execute(RollingRestartState(), _ctx()))
    assert out == {"artifact_id": "compiled-artifact-v1"}


def test_select_artifact_existing_preserved() -> None:
    state = RollingRestartState(artifact_id="custom")
    out = asyncio.run(SelectArtifactNode().execute(state, _ctx()))
    assert out == {}


def test_snapshot_current_pointer() -> None:
    out = asyncio.run(SnapshotCurrentPointerNode().execute(RollingRestartState(), _ctx()))
    assert out["previous_artifact_id"] == "compiled-artifact-v0"


def test_list_worker_batches_passthrough() -> None:
    out = asyncio.run(ListWorkerBatchesNode().execute(RollingRestartState(), _ctx()))
    assert out == {}


def test_restart_batches_emit_envelopes() -> None:
    state = RollingRestartState(artifact_id="art-1")
    for node_cls, idx in [
        (RestartBatch1Node, 1),
        (RestartBatch2Node, 2),
        (RestartBatch3Node, 3),
    ]:
        out = asyncio.run(node_cls().execute(state, _ctx()))
        assert out["last_broker_intent"] == "cve_rem.restart_batch"
        assert out["broker_request_envelope"]["context"]["batch_index"] == idx


def test_health_gate_pass_default() -> None:
    for node_cls, field in [
        (HealthGateBatch1Node, "batch_1_ok"),
        (HealthGateBatch2Node, "batch_2_ok"),
        (HealthGateBatch3Node, "batch_3_ok"),
    ]:
        out = asyncio.run(node_cls().execute(RollingRestartState(), _ctx()))
        assert out[field] is True


def test_health_gate_env_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVE_REM_BATCH_1_OK", "false")
    out = asyncio.run(HealthGateBatch1Node().execute(RollingRestartState(), _ctx()))
    assert out["batch_1_ok"] is False


def test_emit_restart_summary(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitRestartSummaryNode

    state = RollingRestartState(
        artifact_id="art-1", batch_1_ok=True, batch_2_ok=True, batch_3_ok=True
    )
    out = asyncio.run(EmitRestartSummaryNode().execute(state, _ctx()))
    assert out["restart_summary_artifact_ref"].startswith("file://")


def test_rollback_pointer_sets_flag() -> None:
    out = asyncio.run(RollbackPointerNode().execute(RollingRestartState(), _ctx()))
    assert out == {"rollback_triggered": True}


def test_emit_rollback_record(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitRollbackRecordNode

    state = RollingRestartState(
        artifact_id="art-1", previous_artifact_id="art-0"
    )
    out = asyncio.run(EmitRollbackRecordNode().execute(state, _ctx()))
    assert out["restart_summary_artifact_ref"].startswith("file://")
