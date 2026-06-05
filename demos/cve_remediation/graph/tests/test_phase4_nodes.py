# SPDX-License-Identifier: Apache-2.0
"""Tests for Phase 4 CR + execute + verify real node bodies (S3.4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from demos.cve_remediation.graph.real_nodes import (
    CreateChangeRequestNode,
    DivergenceQuarantineNode,
    DriftWatchSpawnNode,
    EmitEvidenceBundleNode,
    HitlChangeApprovalNode,
    PartialApplyRollbackNode,
    ProgressiveExecuteNode,
    VerifyImmediateNode,
)
from demos.cve_remediation.graph.state import (
    CodeRuntime,
    CorrelatedAssets,
    CveRemState,
    RemediationBundle,
    SandboxResult,
    SandboxRuntime,
    SsvcTier,
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
# Create change request
# ---------------------------------------------------------------------------


def test_create_change_request_builds_envelope() -> None:
    state = CveRemState(
        cve_id="CVE-2024-1234",
        plan_hash="deadbeef",
        correlated=CorrelatedAssets(affected_assets=["host-1", "host-2"]),
        code_runtime=CodeRuntime.ANSIBLE,
        ssvc_tier=SsvcTier.ATTEND,
    )
    out = asyncio.run(CreateChangeRequestNode().execute(state, _ctx()))
    assert out["cr_correlation_id"].startswith("CR-")
    assert out["cr_status"] == "draft"
    assert out["last_broker_intent"] == "cve_rem.create_change_request"
    payload = out["broker_request_envelope"]["context"]
    assert payload["cve_id"] == "CVE-2024-1234"
    assert payload["plan_hash"] == "deadbeef"
    assert payload["affected_assets"] == ["host-1", "host-2"]


def test_create_change_request_deterministic() -> None:
    state = CveRemState(cve_id="CVE-X", plan_hash="abc")
    a = asyncio.run(CreateChangeRequestNode().execute(state, _ctx()))
    b = asyncio.run(CreateChangeRequestNode().execute(state, _ctx()))
    assert a["cr_correlation_id"] == b["cr_correlation_id"]


# ---------------------------------------------------------------------------
# Emit evidence bundle
# ---------------------------------------------------------------------------


def test_emit_evidence_bundle_writes_file(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitEvidenceBundleNode

    state = CveRemState(
        cve_id="CVE-X",
        plan_hash="abc",
        cr_correlation_id="CR-DEAD",
        bundle=RemediationBundle(apply_bundle_ref="bundle://x/apply"),
        sandbox=SandboxResult(runtime=SandboxRuntime.DOCKER_COMPOSE, status="ok"),
    )
    out = asyncio.run(EmitEvidenceBundleNode().execute(state, _ctx()))
    assert out["evidence_bundle_artifact_ref"].startswith("file://")
    files = list((isolated_artifacts / "evidence").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["cve_id"] == "CVE-X"
    assert payload["cr_correlation_id"] == "CR-DEAD"
    assert payload["bundle"]["apply_bundle_ref"] == "bundle://x/apply"


# ---------------------------------------------------------------------------
# HITL change approval
# ---------------------------------------------------------------------------


def test_hitl_change_approval_synthesizes_approve() -> None:
    state = CveRemState(cr_correlation_id="CR-XYZ")
    out = asyncio.run(HitlChangeApprovalNode().execute(state, _ctx()))
    assert out["response"].decision == "approve"
    assert "change" in out["hitl_gates"]
    assert out["cr_status"] == "approved"


def test_hitl_persistence_skipped_without_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase F+ (2026-05-11): block path with no POSTGRES_DSN → no-op
    (hitl_persistence_written=False) but block still surfaces."""
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.setenv("CVE_REM_LIVE_BROKER", "1")
    state = CveRemState(
        run_id="run-zz", cve_id="CVE-Z", cr_correlation_id="CR-Z",
    )
    out = asyncio.run(HitlChangeApprovalNode().execute(state, _ctx()))
    assert out["hitl_blocked_at"] == "change_approval"
    assert out["hitl_persistence_written"] is False
    # No response emitted on block.
    assert "response" not in out


# ---------------------------------------------------------------------------
# Progressive execute
# ---------------------------------------------------------------------------


def test_progressive_execute_passes_when_validation_ok() -> None:
    """No affected hosts -> canary:skipped (nothing to deploy), but the
    rollout still counts as ok (no failure occurred). The canary:ok /
    stage:ok / fleet:ok ledger is reserved for the hosts>0 + bundle
    apply path which needs a real bundle + cargonet exec; see
    test_e2e for the full hosts-with-bundle path.
    """
    state = CveRemState(
        validation_passed=True,
        sandbox=SandboxResult(runtime=SandboxRuntime.DOCKER_COMPOSE, status="ok"),
    )
    out = asyncio.run(ProgressiveExecuteNode().execute(state, _ctx()))
    assert out["fleet_passed"] is True
    assert out["rollback_triggered"] is False
    assert out["cr_status"] == "implemented"
    assert out["execution_ledger"] == ["canary:skipped"]


def test_progressive_execute_rollback_when_validation_fail() -> None:
    state = CveRemState(validation_passed=False)
    out = asyncio.run(ProgressiveExecuteNode().execute(state, _ctx()))
    assert out["rollback_triggered"] is True
    assert out["fleet_passed"] is False
    assert out["cr_status"] == "rejected"


def test_progressive_execute_rollback_on_sandbox_fail() -> None:
    state = CveRemState(
        validation_passed=True,
        sandbox=SandboxResult(runtime=SandboxRuntime.DOCKER_COMPOSE, status="fail"),
    )
    out = asyncio.run(ProgressiveExecuteNode().execute(state, _ctx()))
    assert out["rollback_triggered"] is True


def test_progressive_execute_skipped_sandbox_ok() -> None:
    """skip_sandbox path (logic-flaw) still progresses if validation_passed=True."""
    state = CveRemState(
        validation_passed=True,
        sandbox=SandboxResult(runtime=SandboxRuntime.SKIP, status="skipped"),
    )
    out = asyncio.run(ProgressiveExecuteNode().execute(state, _ctx()))
    assert out["fleet_passed"] is True


# ---------------------------------------------------------------------------
# Partial apply rollback
# ---------------------------------------------------------------------------


def test_partial_apply_rollback_appends_ledger() -> None:
    """PartialApplyRollbackNode now guards on rollback_triggered=True so
    it doesn't overwrite a clean ProgressiveExecute success when Fathom
    rules are inactive. Seed both signals."""
    state = CveRemState(
        execution_ledger=["canary:fail"],
        rollback_triggered=True,
    )
    out = asyncio.run(PartialApplyRollbackNode().execute(state, _ctx()))
    assert any("rollback@" in entry for entry in out["execution_ledger"])
    assert out["verify_outcome"] == "vulnerable"
    assert "Rollback" in out["halt_reason"]


# ---------------------------------------------------------------------------
# Verify immediate
# ---------------------------------------------------------------------------


def test_verify_immediate_patched_when_fleet_ok() -> None:
    """VerifyImmediate no longer trusts upstream verify_outcome; it runs
    its own per-host probes.  Without CargoNet or bundle-verify tasks
    available in unit tests, the outcome is ``unverified`` (operator
    triage required) — which is the honest behavior.
    """
    state = CveRemState(
        fleet_passed=True,
        verify_outcome="patched",
        verify_probe_method="ansible-bundle",
    )
    out = asyncio.run(VerifyImmediateNode().execute(state, _ctx()))
    assert out["verify_outcome"] == "unverified"
    assert out["sandbox_prod_divergence"] is False


def test_verify_immediate_divergence_when_sandbox_ok_but_fleet_fail() -> None:
    state = CveRemState(
        fleet_passed=False,
        sandbox=SandboxResult(runtime=SandboxRuntime.DOCKER_COMPOSE, status="ok"),
    )
    out = asyncio.run(VerifyImmediateNode().execute(state, _ctx()))
    assert out["verify_outcome"] == "divergence"
    assert out["sandbox_prod_divergence"] is True


def test_verify_immediate_vulnerable_default() -> None:
    state = CveRemState(fleet_passed=False)
    out = asyncio.run(VerifyImmediateNode().execute(state, _ctx()))
    assert out["verify_outcome"] == "vulnerable"


def test_verify_immediate_mitigation_verified_overrides_stale_halt() -> None:
    """mitigation_only rollout completed with stale halt_reason from
    upstream tier router still routes to mitigation_verified, not
    vulnerable. Regression test for the Phase A fix."""
    state = CveRemState(
        cve_id="CVE-2099-0001",
        fleet_passed=True,
        mitigation_only=True,
        mitigation_probe_passed=True,
        halt_reason="DEFER tier: re-evaluate later",
    )
    out = asyncio.run(VerifyImmediateNode().execute(state, _ctx()))
    assert out["verify_outcome"] == "mitigation_applied"
    assert out["verify_probe_method"] == "mitigation"


def test_verify_immediate_halt_short_circuit_when_mitigation_suppressed() -> None:
    """mitigation_only=True but fleet_passed=False (suppression fired)
    still honors halt_reason short-circuit; mitigation_verified does
    not fire because rollout never ran."""
    state = CveRemState(
        cve_id="CVE-2099-0002",
        fleet_passed=False,
        mitigation_only=True,
        halt_reason="not_applicable: no host coverage (cmdb_match_quality=miss)",
    )
    out = asyncio.run(VerifyImmediateNode().execute(state, _ctx()))
    assert out["verify_outcome"] == "vulnerable"
    assert out["verify_probe_method"] == "none"


def test_verify_immediate_unpatchable_pending_hitl() -> None:
    """Phase C: when ProgressiveExecute halted on unpatchable +
    halt_reason starts with unpatchable_pending_hitl, verify emits
    a distinct outcome so retro classifies the CR correctly."""
    state = CveRemState(
        cve_id="CVE-2099-0003",
        fleet_passed=False,
        unpatchable_disposition="disable_recommended",
        unpatchable_reason="No upstream fix; CISA KEV listed",
        halt_reason=(
            "unpatchable_pending_hitl: disable_recommended"
        ),
    )
    out = asyncio.run(VerifyImmediateNode().execute(state, _ctx()))
    assert out["verify_outcome"] == "unpatchable_hitl_pending"
    assert out["verify_probe_method"] == "unpatchable"


# ---------------------------------------------------------------------------
# Divergence quarantine
# ---------------------------------------------------------------------------


def test_divergence_quarantine_writes_artifact(isolated_artifacts: Path) -> None:
    """DivergenceQuarantineNode now guards on sandbox_prod_divergence=True
    so it doesn't overwrite a clean verify_outcome=patched. The flag is
    set upstream by VerifyImmediateNode when sandbox-prod evidence
    actually disagrees (sandbox patched / per-host vulnerable)."""
    from demos.cve_remediation.graph.real_nodes import DivergenceQuarantineNode

    state = CveRemState(
        cve_id="CVE-X",
        plan_hash="abc",
        sandbox=SandboxResult(runtime=SandboxRuntime.DOCKER_COMPOSE, status="ok"),
        sandbox_prod_divergence=True,
    )
    out = asyncio.run(DivergenceQuarantineNode().execute(state, _ctx()))
    files = list((isolated_artifacts / "divergence").glob("*.json"))
    assert len(files) == 1
    assert any("divergence@" in e for e in out["drift_events"])
    assert out["verify_outcome"] == "divergence"


# ---------------------------------------------------------------------------
# Drift watch spawn
# ---------------------------------------------------------------------------


def test_drift_watch_spawn_builds_envelope() -> None:
    state = CveRemState(cve_id="CVE-Y", run_id="run-123")
    out = asyncio.run(DriftWatchSpawnNode().execute(state, _ctx()))
    assert out["last_broker_intent"] == "cve_rem.drift_watch_spawn"
    payload = out["broker_request_envelope"]["context"]
    assert payload["cve_id"] == "CVE-Y"
    assert payload["parent_run_id"] == "run-123"
    assert any("drift_watch_spawn@" in e for e in out["drift_events"])
