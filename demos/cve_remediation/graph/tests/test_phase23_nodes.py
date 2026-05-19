# SPDX-License-Identifier: Apache-2.0
"""Tests for Phase 2/3 plan + sandbox real node bodies (S3.2/S3.3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from demos.cve_remediation.graph.real_nodes import (
    CargonetLabTelemetryNode,
    CodeWriterNode,
    CriticNode,
    EmitRemediationBundleNode,
    EmitSandboxEvidenceNode,
    FrameworkMappingNode,
    GraphBlastRadiusNode,
    GraphPriorRemediationsNode,
    HitlPlanReviewNode,
    JudgeLintNode,
    JudgeSafetyNode,
    PlanTemplateLookupNode,
    PlannerNode,
    SandboxDispatchNode,
    SandboxRunNode,
    SandboxSkipNode,
    SuppressNotApplicableNode,
    TierTerminalDeferNode,
    TierTerminalTrackNode,
    ValidatePlanJoinNode,
    VecSearchRetrosNode,
)
from demos.cve_remediation.graph.state import (
    CodeRuntime,
    CorrelatedAssets,
    CveExtract,
    CveRemState,
    RemediationBundle,
    SandboxRuntime,
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
# Phase 2 terminals
# ---------------------------------------------------------------------------


def test_suppress_not_applicable() -> None:
    out = asyncio.run(SuppressNotApplicableNode().execute(CveRemState(), _ctx()))
    assert "Not applicable" in out["halt_reason"]


def test_tier_terminal_track() -> None:
    state = CveRemState(cve_id="CVE-X")
    out = asyncio.run(TierTerminalTrackNode().execute(state, _ctx()))
    assert "TRACK" in out["halt_reason"]
    assert "CVE-X" in out["halt_reason"]


def test_tier_terminal_defer() -> None:
    state = CveRemState(cve_id="CVE-Y")
    out = asyncio.run(TierTerminalDeferNode().execute(state, _ctx()))
    assert "DEFER" in out["halt_reason"]


# ---------------------------------------------------------------------------
# Phase 3 plan_template_lookup
# ---------------------------------------------------------------------------


def test_plan_template_lookup_hit() -> None:
    state = CveRemState(extract=CveExtract(cwe_class="CWE-502", vuln_class="library"))
    out = asyncio.run(PlanTemplateLookupNode().execute(state, _ctx()))
    assert out["template_lookup_hit"] is True
    assert len(out["plan_hash"]) == 16


def test_plan_template_lookup_miss() -> None:
    state = CveRemState(extract=CveExtract(cwe_class="CWE-9999", vuln_class="exotic"))
    out = asyncio.run(PlanTemplateLookupNode().execute(state, _ctx()))
    assert out["template_lookup_hit"] is False
    assert "no template" in out["template_lookup_miss_reason"]


# ---------------------------------------------------------------------------
# Phase 3 retrieval fan-out
# ---------------------------------------------------------------------------


def test_retrieval_appends_marker() -> None:
    state = CveRemState()
    out = asyncio.run(VecSearchRetrosNode().execute(state, _ctx()))
    assert out["broker_request_envelope"]["retrievals"] == ["vec_search_retros"]


def test_retrieval_chain_idempotent() -> None:
    state = CveRemState()
    out = asyncio.run(VecSearchRetrosNode().execute(state, _ctx()))
    state = state.model_copy(update=out)
    out = asyncio.run(GraphPriorRemediationsNode().execute(state, _ctx()))
    state = state.model_copy(update=out)
    out = asyncio.run(FrameworkMappingNode().execute(state, _ctx()))
    state = state.model_copy(update=out)
    out = asyncio.run(CargonetLabTelemetryNode().execute(state, _ctx()))
    assert set(out["broker_request_envelope"]["retrievals"]) == {
        "vec_search_retros",
        "graph_prior_remediations",
        "framework_mapping",
        "cargonet_lab_telemetry",
    }


def test_graph_blast_radius_kev() -> None:
    state = CveRemState(extract=CveExtract(kev_listed=True))
    out = asyncio.run(GraphBlastRadiusNode().execute(state, _ctx()))
    assert out["correlated"].blast_radius_node_count == 250


def test_graph_blast_radius_high_cvss() -> None:
    state = CveRemState(extract=CveExtract(cvss_score_bp=950))
    out = asyncio.run(GraphBlastRadiusNode().execute(state, _ctx()))
    assert out["correlated"].blast_radius_node_count == 100


def test_graph_blast_radius_low_cvss() -> None:
    state = CveRemState(extract=CveExtract(cvss_score_bp=300))
    out = asyncio.run(GraphBlastRadiusNode().execute(state, _ctx()))
    assert out["correlated"].blast_radius_node_count == 0


# ---------------------------------------------------------------------------
# Planner / code_writer / critic
# ---------------------------------------------------------------------------


def test_planner_picks_runtimes_from_vuln_class() -> None:
    state = CveRemState(
        cve_id="CVE-Z", extract=CveExtract(cwe_class="CWE-79", vuln_class="web-framework")
    )
    out = asyncio.run(PlannerNode().execute(state, _ctx()))
    assert out["sandbox_runtime"] == "docker_compose"
    assert out["code_runtime"] == "ansible"
    assert len(out["plan_hash"]) == 16


def test_planner_deterministic() -> None:
    state = CveRemState(
        cve_id="CVE-Z", extract=CveExtract(cwe_class="CWE-79", vuln_class="web-framework")
    )
    a = asyncio.run(PlannerNode().execute(state, _ctx()))
    b = asyncio.run(PlannerNode().execute(state, _ctx()))
    assert a["plan_hash"] == b["plan_hash"]


def test_planner_unknown_vuln_class_defaults() -> None:
    state = CveRemState(extract=CveExtract(vuln_class="exotic"))
    out = asyncio.run(PlannerNode().execute(state, _ctx()))
    assert out["sandbox_runtime"] == "docker_compose"
    assert out["code_runtime"] == "ansible"


def test_code_writer_emits_bundle() -> None:
    state = CveRemState(plan_hash="deadbeef", code_runtime=CodeRuntime.K8S)
    out = asyncio.run(CodeWriterNode().execute(state, _ctx()))
    bundle = out["bundle"]
    assert bundle.runtime == CodeRuntime.K8S
    # Phase F: apply/rollback refs are real file:// paths to written
    # Ansible playbooks (the synthetic ``bundle://`` scheme is gone).
    # verify_probe_ref stays a synthetic ``probe://`` URI since the
    # probe handle is still resolved at sandbox-run time.
    assert bundle.apply_bundle_ref.startswith("file://")
    assert bundle.apply_bundle_ref.endswith("deadbeef_apply.yaml")
    assert bundle.rollback_bundle_ref.startswith("file://")
    assert bundle.rollback_bundle_ref.endswith("deadbeef_rollback.yaml")
    assert bundle.verify_probe_ref == "probe://deadbeef/verify"


def test_code_writer_uses_plan_spec_when_complete(isolated_artifacts: Path) -> None:
    """Phase F: plan_spec complete → deterministic primitives path."""
    state = CveRemState(
        plan_hash="cafef00d",
        code_runtime=CodeRuntime.ANSIBLE,
        install_channel="apt",
        plan_spec={
            "honest_skip": False,
            "apply": {
                "intent": "downgrade xz-utils",
                "primitive": "downgrade",
                "target": "xz-utils",
                "target_version": "5.4.5-1",
                "cite_url": "https://debian.org/X",
                "action_ref": 0,
            },
            "verify": {
                "intent": "probe installed version",
                "primitive": "probe",
                "target": "xz-utils",
                "target_version": "5.4.5-1",
                "action_ref": 0,
            },
            "rollback": {
                "intent": "upgrade xz-utils",
                "primitive": "upgrade",
                "target": "xz-utils",
                "target_version": "5.6.2",
                "action_ref": 0,
            },
            "regression": {
                "intent": "healthcheck",
                "primitive": "healthcheck",
                "target": "xz-utils",
                "action_ref": 0,
            },
            "schema_version": 1,
            "deficit_reasons": [],
        },
    )
    out = asyncio.run(CodeWriterNode().execute(state, _ctx()))
    bundle = out["bundle"]
    assert bundle.metadata.get("generated_by") == "plan_spec_deterministic"
    assert bundle.metadata.get("rollback_non_invertible") == "false"
    assert bundle.apply_bundle_ref.endswith("cafef00d_apply.yaml")
    # The persisted YAML contains the deterministic install command.
    apply_path = bundle.apply_bundle_ref.removeprefix("file://")
    apply_yaml = Path(apply_path).read_text(encoding="utf-8")
    assert "xz-utils=5.4.5-1" in apply_yaml
    rollback_path = bundle.rollback_bundle_ref.removeprefix("file://")
    rollback_yaml = Path(rollback_path).read_text(encoding="utf-8")
    assert "xz-utils=5.6.2" in rollback_yaml


def test_code_writer_plan_spec_non_invertible_upgrade(isolated_artifacts: Path) -> None:
    """Phase F: upgrade with empty rollback version → meta flag set."""
    state = CveRemState(
        plan_hash="cafe1234",
        code_runtime=CodeRuntime.ANSIBLE,
        install_channel="apt",
        plan_spec={
            "honest_skip": False,
            "apply": {
                "primitive": "upgrade",
                "target": "openssl",
                "target_version": "3.0.10",
                "intent": "upgrade openssl",
                "action_ref": 0,
            },
            "verify": {
                "primitive": "probe", "target": "openssl",
                "target_version": "3.0.10",
                "intent": "probe", "action_ref": 0,
            },
            "rollback": {
                "primitive": "downgrade", "target": "openssl",
                "target_version": "",
                "intent": "downgrade", "action_ref": 0,
            },
            "regression": {
                "primitive": "healthcheck", "target": "openssl",
                "intent": "hc", "action_ref": 0,
            },
            "schema_version": 1,
        },
    )
    out = asyncio.run(CodeWriterNode().execute(state, _ctx()))
    bundle = out["bundle"]
    assert bundle.metadata.get("rollback_non_invertible") == "true"
    assert bundle.metadata.get("rollback_reason") == "no_prior_version_known"


def test_code_writer_falls_back_when_plan_spec_empty(isolated_artifacts: Path) -> None:
    """Phase F: empty plan_spec → LM bundle path stays in control."""
    state = CveRemState(plan_hash="deadc0de", code_runtime=CodeRuntime.ANSIBLE)
    out = asyncio.run(CodeWriterNode().execute(state, _ctx()))
    bundle = out["bundle"]
    # No plan_spec → not deterministic path
    assert bundle.metadata.get("generated_by") != "plan_spec_deterministic"


def test_emit_remediation_bundle_writes_file(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitRemediationBundleNode

    state = CveRemState(
        bundle=RemediationBundle(
            runtime=CodeRuntime.ANSIBLE,
            apply_bundle_ref="bundle://x/apply",
            rollback_bundle_ref="bundle://x/rollback",
            verify_probe_ref="probe://x/verify",
        )
    )
    out = asyncio.run(EmitRemediationBundleNode().execute(state, _ctx()))
    target_dir = isolated_artifacts / "remediation"
    assert target_dir.is_dir()
    files = list(target_dir.glob("*.json"))
    assert len(files) == 1
    assert out["remediation_bundle_artifact_ref"].startswith("file://")


def test_critic_approved_when_clean() -> None:
    state = CveRemState(
        bundle=RemediationBundle(
            apply_bundle_ref="bundle://x/apply",
            rollback_bundle_ref="bundle://x/rollback",
            verify_probe_ref="probe://x/verify",
        ),
        correlated=CorrelatedAssets(blast_radius_node_count=10),
        code_runtime=CodeRuntime.ANSIBLE,
    )
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    assert out["critic_verdict"] == "approved"


def test_critic_veto_incomplete_bundle() -> None:
    state = CveRemState(bundle=RemediationBundle(apply_bundle_ref=""))
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    assert out["critic_verdict"] == "veto"


def test_critic_feedback_high_blast() -> None:
    state = CveRemState(
        bundle=RemediationBundle(
            apply_bundle_ref="bundle://x/apply",
            rollback_bundle_ref="bundle://x/rollback",
            verify_probe_ref="probe://x/verify",
        ),
        correlated=CorrelatedAssets(blast_radius_node_count=200),
        code_runtime=CodeRuntime.K8S,
    )
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    assert out["critic_verdict"] == "feedback"


def test_critic_attempt_increments() -> None:
    state = CveRemState(
        bundle=RemediationBundle(apply_bundle_ref=""),
        critic_attempt=2,
    )
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    assert out["critic_attempt"] == 3


def test_critic_emits_structured_deficits_on_incomplete() -> None:
    """Phase F: critic emits structured deficits alongside text feedback."""
    state = CveRemState(bundle=RemediationBundle(apply_bundle_ref=""))
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    kinds = [d["kind"] for d in out["critic_deficits"]]
    assert "missing_apply" in kinds
    assert "missing_rollback" in kinds
    assert "missing_verify_probe" in kinds


def test_critic_merges_plan_spec_deficits() -> None:
    """Phase F: planner-detected deficits flow through critic_deficits."""
    state = CveRemState(
        bundle=RemediationBundle(
            apply_bundle_ref="bundle://x/apply",
            rollback_bundle_ref="bundle://x/rollback",
            verify_probe_ref="probe://x/verify",
        ),
        correlated=CorrelatedAssets(blast_radius_node_count=10),
        plan_spec_deficits=[
            {"kind": "version_unspecified", "slot": "apply", "detail": "foo"},
        ],
    )
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    assert out["critic_verdict"] == "approved"
    kinds = [d["kind"] for d in out["critic_deficits"]]
    assert "version_unspecified" in kinds


def test_critic_surfaces_non_invertible_rollback_from_metadata() -> None:
    """Phase F: bundle.metadata rollback_non_invertible → deficit emit."""
    state = CveRemState(
        bundle=RemediationBundle(
            apply_bundle_ref="bundle://x/apply",
            rollback_bundle_ref="bundle://x/rollback",
            verify_probe_ref="probe://x/verify",
            metadata={
                "generated_by": "plan_spec_deterministic",
                "rollback_non_invertible": "true",
                "rollback_reason": "no_prior_version_known",
            },
        ),
        correlated=CorrelatedAssets(blast_radius_node_count=10),
    )
    out = asyncio.run(CriticNode().execute(state, _ctx()))
    kinds = [d["kind"] for d in out["critic_deficits"]]
    assert "non_invertible_rollback" in kinds


# ---------------------------------------------------------------------------
# HITL plan / judges / validate join
# ---------------------------------------------------------------------------


def test_hitl_plan_review_synthesizes_approve() -> None:
    state = CveRemState(plan_hash="abc")
    out = asyncio.run(HitlPlanReviewNode().execute(state, _ctx()))
    assert out["response"].decision == "approve"
    assert "plan" in out["hitl_gates"]


def test_judge_safety_pass_when_approved_and_clean() -> None:
    state = CveRemState(critic_verdict="approved", untrusted_text_influenced=False)
    out = asyncio.run(JudgeSafetyNode().execute(state, _ctx()))
    assert out["judge_safety_verdict"] == "pass"


def test_judge_safety_fail_when_influenced() -> None:
    state = CveRemState(critic_verdict="approved", untrusted_text_influenced=True)
    out = asyncio.run(JudgeSafetyNode().execute(state, _ctx()))
    assert out["judge_safety_verdict"] == "fail"


def test_judge_lint_pass_when_bundle_well_formed() -> None:
    state = CveRemState(
        bundle=RemediationBundle(
            apply_bundle_ref="bundle://x/apply",
            rollback_bundle_ref="bundle://x/rollback",
            verify_probe_ref="probe://x/verify",
        )
    )
    out = asyncio.run(JudgeLintNode().execute(state, _ctx()))
    assert out["judge_lint_verdict"] == "pass"


def test_judge_lint_fail_on_malformed() -> None:
    state = CveRemState(bundle=RemediationBundle(apply_bundle_ref="bogus"))
    out = asyncio.run(JudgeLintNode().execute(state, _ctx()))
    assert out["judge_lint_verdict"] == "fail"


def test_validate_plan_join_and() -> None:
    state = CveRemState(judge_safety_verdict="pass", judge_lint_verdict="pass")
    out = asyncio.run(ValidatePlanJoinNode().execute(state, _ctx()))
    assert out["validation_passed"] is True


def test_validate_plan_join_fail_on_lint() -> None:
    state = CveRemState(judge_safety_verdict="pass", judge_lint_verdict="fail")
    out = asyncio.run(ValidatePlanJoinNode().execute(state, _ctx()))
    assert out["validation_passed"] is False


# ---------------------------------------------------------------------------
# Sandbox dispatch / run / skip / evidence
# ---------------------------------------------------------------------------


def test_sandbox_dispatch_picks_runtime() -> None:
    state = CveRemState(extract=CveExtract(vuln_class="library"))
    out = asyncio.run(SandboxDispatchNode().execute(state, _ctx()))
    assert out["sandbox_runtime"] == SandboxRuntime.DOCKER_COMPOSE


def test_sandbox_dispatch_logic_flaw_skips() -> None:
    state = CveRemState(extract=CveExtract(vuln_class="logic-flaw"))
    out = asyncio.run(SandboxDispatchNode().execute(state, _ctx()))
    assert out["sandbox_runtime"] == SandboxRuntime.SKIP


def test_sandbox_run_emits_ok_result() -> None:
    # CARGONET_LAB runtime with no proxy_ref + no docker dependency falls
    # to the deterministic plan-hash fallback, which is the offline-replay
    # path the unit test asserts. DOCKER_COMPOSE now requires a real
    # docker daemon AND sufficient advisory signal -- both unavailable in
    # a unit test, so SandboxRunNode honest-skips that branch.
    state = CveRemState(
        sandbox_runtime=SandboxRuntime.CARGONET_LAB, plan_hash="abc"
    )
    out = asyncio.run(SandboxRunNode().execute(state, _ctx()))
    assert out["sandbox_status"] == "ok"
    assert out["sandbox"].apply_probe.endswith("/apply")


def test_sandbox_run_skip_path() -> None:
    state = CveRemState(sandbox_runtime=SandboxRuntime.SKIP)
    out = asyncio.run(SandboxRunNode().execute(state, _ctx()))
    assert out["sandbox_status"] == "skipped"
    assert out["sandbox"].force_hitl is True


def test_sandbox_skip_sets_force_hitl() -> None:
    out = asyncio.run(SandboxSkipNode().execute(CveRemState(), _ctx()))
    assert out["skip_sandbox"] is True
    assert out["sandbox_status"] == "skipped"


def test_emit_sandbox_evidence_writes_file(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitSandboxEvidenceNode
    from demos.cve_remediation.graph.state import SandboxResult

    state = CveRemState(
        sandbox=SandboxResult(
            runtime=SandboxRuntime.DOCKER_COMPOSE,
            status="ok",
            apply_probe="probe://x/apply",
        )
    )
    out = asyncio.run(EmitSandboxEvidenceNode().execute(state, _ctx()))
    assert out["sandbox_evidence_artifact_ref"].startswith("file://")
    target_dir = isolated_artifacts / "sandbox"
    files = list(target_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["runtime"] == "docker_compose"
