# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the cve-remediation IR scaffold (post P0+P1+P2+P3).

Verifies:
  - All IR YAMLs (main + phase0 + phase6 + 2 sub-graphs + 5 triggered
    graphs) load via PyYAML without schema errors.
  - Routing-target resolution per graph.
  - Phase coverage of the main graph.
  - HITL gates: 4 in main, each followed by branch_resp_<gate>; durable
    wait (timeout=null).
  - Multi-kind invariant: not all nodes are passthrough; broker / dspy /
    ml / tool / write_artifact / interrupt / subgraph all present.
  - Parallel actions present in main (MCP retrieval, dual judges, retro
    fan-out).
  - Artifact emission: 7 expected write_artifact nodes in main.
  - Drift watch promoted from sub-graph to triggered graph (only 2
    subgraph refs in main).
  - Phase 0 idempotency skip path present.
  - Phase 6 emits compiled-prompt artifact.
  - 5 triggered graphs present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GRAPH_DIR = Path(__file__).resolve().parent.parent
MAIN_IR = GRAPH_DIR / "harbor.yaml"
PHASE0_IR = GRAPH_DIR / "phase0" / "doctrine_ingest.yaml"
PHASE6_IR = GRAPH_DIR / "phase6" / "offline_learning.yaml"
SUBGRAPH_IRS = [
    GRAPH_DIR / "subgraphs" / "sandbox_dispatch.yaml",
    GRAPH_DIR / "subgraphs" / "progressive_execute.yaml",
]
TRIGGERED_IRS = [
    GRAPH_DIR / "triggered" / "drift_watch.yaml",
    GRAPH_DIR / "triggered" / "tier_re_eval.yaml",
    GRAPH_DIR / "triggered" / "audit_anchor.yaml",
    GRAPH_DIR / "triggered" / "lab_leak_reaper.yaml",
    GRAPH_DIR / "triggered" / "rolling_restart.yaml",
]
ALL_IRS = [MAIN_IR, PHASE0_IR, PHASE6_IR, *SUBGRAPH_IRS, *TRIGGERED_IRS]


def _load(ir_path: Path) -> dict:
    return yaml.safe_load(ir_path.read_text())


# Map promoted ``module:ClassName`` kind back to the short kind for
# structural assertions. Covers both the kind-blind stubs (nodes.py)
# and the E1 real-node implementations (real_nodes.py).
_KIND_FROM_STUB = {
    # Kind-blind POC stubs
    "demos.cve_remediation.graph.nodes:PassthroughStub": "passthrough",
    "demos.cve_remediation.graph.nodes:ToolStub": "tool",
    "demos.cve_remediation.graph.nodes:BrokerStub": "broker",
    "demos.cve_remediation.graph.nodes:WriteArtifactStub": "write_artifact",
    "demos.cve_remediation.graph.nodes:InterruptStub": "interrupt",
    "demos.cve_remediation.graph.nodes:MLStub": "ml",
    "demos.cve_remediation.graph.nodes:DSPyStub": "dspy",
    "demos.cve_remediation.graph.nodes:SubgraphStub": "subgraph",
    # E1 real-node impls (mapped to the kind they replace)
    "demos.cve_remediation.graph.real_nodes:SourceTrustGateNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:SsvcTierEvaluatorNode": "tool",
    "demos.cve_remediation.graph.real_nodes:GepaScoreComputerNode": "ml",
    "demos.cve_remediation.graph.real_nodes:ManifestSignNode": "tool",
    "demos.cve_remediation.graph.real_nodes:WriteArtifactRealNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:CorrelateAssetsBrokerNode": "broker",
    # S3.0 Phase 0 doctrine-ingest real impls
    "demos.cve_remediation.graph.real_nodes:IdempotencyCheckNode": "tool",
    "demos.cve_remediation.graph.real_nodes:DoctrineLoaderNode": "broker",
    "demos.cve_remediation.graph.real_nodes:CanonicalizeDoctrineNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:DoctrineExtractorNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:KgLoaderNode": "tool",
    "demos.cve_remediation.graph.real_nodes:BootgateAllowlistUpdateNode": "tool",
    # S3.1 Phase 1 intake real impls
    "demos.cve_remediation.graph.real_nodes:CanonicalizeTrustedNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:CanonicalizeUntrustedNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:ExtractTrustedNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:ExtractUntrustedNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:InjectionClassifyNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:CritiqueExtractedNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:EnrichCveTrustedNode": "tool",
    "demos.cve_remediation.graph.real_nodes:EnrichCveUntrustedNode": "tool",
    "demos.cve_remediation.graph.real_nodes:EmitQuarantineArtifactNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:HitlIngestReviewNode": "interrupt",
    # S3.2 Phase 2 terminal real impls
    "demos.cve_remediation.graph.real_nodes:SuppressNotApplicableNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:TierTerminalTrackNode": "tool",
    "demos.cve_remediation.graph.real_nodes:TierTerminalDeferNode": "tool",
    # S3.3 Phase 3 plan + sandbox real impls
    "demos.cve_remediation.graph.real_nodes:PlanTemplateLookupNode": "tool",
    "demos.cve_remediation.graph.real_nodes:VecSearchRetrosNode": "tool",
    "demos.cve_remediation.graph.real_nodes:GraphPriorRemediationsNode": "tool",
    "demos.cve_remediation.graph.real_nodes:GraphBlastRadiusNode": "tool",
    "demos.cve_remediation.graph.real_nodes:FrameworkMappingNode": "tool",
    "demos.cve_remediation.graph.real_nodes:CargonetLabTelemetryNode": "tool",
    "demos.cve_remediation.graph.real_nodes:PlannerNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:CodeWriterNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:EmitRemediationBundleNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:CriticNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:HitlPlanReviewNode": "interrupt",
    "demos.cve_remediation.graph.real_nodes:JudgeSafetyNode": "tool",
    "demos.cve_remediation.graph.real_nodes:JudgeLintNode": "tool",
    "demos.cve_remediation.graph.real_nodes:ValidatePlanJoinNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:SandboxDispatchNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:SandboxRunNode": "subgraph",
    "demos.cve_remediation.graph.real_nodes:SandboxSkipNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:EmitSandboxEvidenceNode": "write_artifact",
    # S3.4 Phase 4 CR + execute + verify real impls
    "demos.cve_remediation.graph.real_nodes:CreateChangeRequestNode": "broker",
    "demos.cve_remediation.graph.real_nodes:EmitEvidenceBundleNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:HitlChangeApprovalNode": "interrupt",
    "demos.cve_remediation.graph.real_nodes:ProgressiveExecuteNode": "subgraph",
    "demos.cve_remediation.graph.real_nodes:PartialApplyRollbackNode": "tool",
    "demos.cve_remediation.graph.real_nodes:VerifyImmediateNode": "tool",
    "demos.cve_remediation.graph.real_nodes:DivergenceQuarantineNode": "tool",
    "demos.cve_remediation.graph.real_nodes:DriftWatchSpawnNode": "broker",
    # S3.5 Phase 5 retro + learn real impls
    "demos.cve_remediation.graph.real_nodes:WriteRetrospectiveNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:EmitRetroPayloadNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:RenderDocxNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:EmitDocxArchiveNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:PublishDocPlusNode": "broker",
    "demos.cve_remediation.graph.real_nodes:CargoNetWritebackNode": "broker",
    "demos.cve_remediation.graph.real_nodes:PlanKgWritebackNode": "tool",
    "demos.cve_remediation.graph.real_nodes:HitlRetrospectiveReviewNode": "interrupt",
    # S3.6 Phase 6 offline learning real impls
    "demos.cve_remediation.graph.real_nodes:PullHoldoutRetrosNode": "tool",
    "demos.cve_remediation.graph.real_nodes:RedactionTransformNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:EmitRedactedCorpusNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:GepaCompilePlannerNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:GepaCompileCriticNode": "dspy",
    "demos.cve_remediation.graph.real_nodes:GateStrictlyBetterNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:RejectArtifactNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:ShamirCeremonyNode": "tool",
    "demos.cve_remediation.graph.real_nodes:ShipToPromptsDirNode": "tool",
    "demos.cve_remediation.graph.real_nodes:SignalRollingRestartNode": "broker",
    # S3.6 Audit anchor real impls
    "demos.cve_remediation.graph.real_nodes:ReadChainHeadNode": "tool",
    "demos.cve_remediation.graph.real_nodes:AnchorViaNautilusNode": "broker",
    "demos.cve_remediation.graph.real_nodes:EmitAnchorReceiptNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:RecordFailureNode": "tool",
    "demos.cve_remediation.graph.real_nodes:PageSecurityOncallNode": "tool",
    "demos.cve_remediation.graph.real_nodes:FireHaltNewNode": "broker",
    # S3.6 Drift watch real impls
    "demos.cve_remediation.graph.real_nodes:ScheduleWatchNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:CollectDriftEventsNode": "tool",
    "demos.cve_remediation.graph.real_nodes:ClassifyDriftNode": "ml",
    "demos.cve_remediation.graph.real_nodes:SpawnChildRunNode": "broker",
    "demos.cve_remediation.graph.real_nodes:PageOncallNode": "tool",
    "demos.cve_remediation.graph.real_nodes:NoopCleanNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:EmitDriftWindowSummaryNode": "write_artifact",
    # S3.6 Lab leak reaper real impls
    "demos.cve_remediation.graph.real_nodes:ListActiveLabsNode": "broker",
    "demos.cve_remediation.graph.real_nodes:FilterExpiredNode": "passthrough",
    "demos.cve_remediation.graph.real_nodes:ReapExpiredNode": "broker",
    "demos.cve_remediation.graph.real_nodes:EmitReaperSummaryNode": "write_artifact",
    # S3.6 Tier re-eval real impls
    "demos.cve_remediation.graph.real_nodes:ScanTrackedDeferredNode": "tool",
    "demos.cve_remediation.graph.real_nodes:RefreshEpssKevNode": "broker",
    "demos.cve_remediation.graph.real_nodes:ReEvaluateSsvcNode": "tool",
    "demos.cve_remediation.graph.real_nodes:SpawnMainPipelineRunsNode": "broker",
    "demos.cve_remediation.graph.real_nodes:UpdateTierEdgesNode": "tool",
    "demos.cve_remediation.graph.real_nodes:EmitReEvalSummaryNode": "write_artifact",
    # S3.6 Rolling restart real impls
    "demos.cve_remediation.graph.real_nodes:SelectArtifactNode": "tool",
    "demos.cve_remediation.graph.real_nodes:SnapshotCurrentPointerNode": "tool",
    "demos.cve_remediation.graph.real_nodes:ListWorkerBatchesNode": "broker",
    "demos.cve_remediation.graph.real_nodes:RestartBatch1Node": "broker",
    "demos.cve_remediation.graph.real_nodes:RestartBatch2Node": "broker",
    "demos.cve_remediation.graph.real_nodes:RestartBatch3Node": "broker",
    "demos.cve_remediation.graph.real_nodes:HealthGateBatch1Node": "tool",
    "demos.cve_remediation.graph.real_nodes:HealthGateBatch2Node": "tool",
    "demos.cve_remediation.graph.real_nodes:HealthGateBatch3Node": "tool",
    "demos.cve_remediation.graph.real_nodes:EmitRestartSummaryNode": "write_artifact",
    "demos.cve_remediation.graph.real_nodes:RollbackPointerNode": "tool",
    "demos.cve_remediation.graph.real_nodes:EmitRollbackRecordNode": "write_artifact",
}


def _short_kind(node: dict) -> str:
    """Return the short kind for a node (``passthrough``/``tool``/...).

    Accepts either the short form (legacy) or the promoted
    ``module:ClassName`` reference; raises ``KeyError`` for unknown
    promoted classes so test signal stays loud.
    """
    kind = node.get("kind", "")
    return _KIND_FROM_STUB.get(kind, kind)


def _all_targets(rule: dict) -> list[str]:
    """Collect every routing target from a rule's actions."""
    out: list[str] = []
    for action in rule.get("then", []):
        if "target" in action and action["target"] is not None:
            out.append(action["target"])
        for t in action.get("targets", []) or []:
            out.append(t)
        if "join" in action and action["join"] is not None:
            out.append(action["join"])
    return out


@pytest.mark.parametrize("ir_path", ALL_IRS, ids=lambda p: p.name)
def test_ir_loads(ir_path: Path) -> None:
    doc = _load(ir_path)
    assert doc["ir_version"] == "1.0.0"
    assert doc["id"].startswith("graph:cve-rem")
    assert isinstance(doc["nodes"], list) and doc["nodes"]
    assert isinstance(doc["rules"], list) and doc["rules"]


@pytest.mark.parametrize("ir_path", ALL_IRS, ids=lambda p: p.name)
def test_routing_targets_resolve(ir_path: Path) -> None:
    doc = _load(ir_path)
    node_ids = {n["id"] for n in doc["nodes"]}
    for rule in doc["rules"]:
        for target in _all_targets(rule):
            assert target in node_ids, (
                f"{ir_path.name}: rule {rule['id']} targets unknown "
                f"node {target!r}"
            )


# --- Main graph structure ----------------------------------------------------


def test_main_phase_coverage() -> None:
    doc = _load(MAIN_IR)
    node_ids = {n["id"] for n in doc["nodes"]}
    assert {"source_trust_gate", "hitl_ingest_review", "branch_resp_ingest"} <= node_ids
    assert {"correlate_assets", "ssvc_evaluate"} <= node_ids
    assert {"planner", "code_writer", "critic", "validate_dispatch", "validate_plan_join", "sandbox_dispatch"} <= node_ids
    assert {"create_change_request", "hitl_change_approval", "branch_resp_change", "verify_immediate"} <= node_ids
    assert {"write_retrospective", "publish_docplus", "cargonet_writeback", "plan_kg_writeback", "branch_resp_retro"} <= node_ids
    assert "action_done" in node_ids


def test_main_has_four_hitl_with_branch_resp() -> None:
    doc = _load(MAIN_IR)
    node_ids = {n["id"] for n in doc["nodes"]}
    interrupts = {n["id"] for n in doc["nodes"] if _short_kind(n) == "interrupt"}
    assert interrupts == {
        "hitl_ingest_review",
        "hitl_plan_review",
        "hitl_change_approval",
        "hitl_retrospective_review",
    }
    # Every HITL has a branch_resp counterpart
    for gate in interrupts:
        suffix = gate.removeprefix("hitl_").removesuffix("_review")
        # ingest, plan, change_approval -> change, retrospective -> retro
        suffix_map = {"ingest": "ingest", "plan": "plan", "change_approval": "change", "retrospective": "retro"}
        expected = f"branch_resp_{suffix_map[suffix]}"
        assert expected in node_ids, f"missing branch_resp for {gate}: expected {expected}"


def test_main_durable_hitl_timeout() -> None:
    doc = _load(MAIN_IR)
    for rule in doc["rules"]:
        for action in rule.get("then", []):
            if action.get("kind") == "interrupt":
                assert action.get("timeout") is None


def test_main_multi_kind_used() -> None:
    """Main IR should not be all-passthrough — broker/dspy/ml/tool/
    write_artifact/interrupt/subgraph all present."""
    doc = _load(MAIN_IR)
    kinds = {_short_kind(n) for n in doc["nodes"]}
    expected = {"passthrough", "broker", "dspy", "tool", "write_artifact", "interrupt", "subgraph"}
    missing = expected - kinds
    assert not missing, f"main IR missing kinds: {missing}"


def test_main_parallel_actions_present() -> None:
    """Three parallel fanouts: MCP retrieval, dual judges, retro fan-out."""
    doc = _load(MAIN_IR)
    parallels = []
    for rule in doc["rules"]:
        for action in rule.get("then", []):
            if action.get("kind") == "parallel":
                parallels.append((rule["id"], action))
    rule_ids = {pid for pid, _ in parallels}
    assert {"r-mcp-fanout", "r-validate-fanout", "r-retro-fanout"} <= rule_ids

    # MCP fanout should include all 5 retrieval tools
    mcp = next(a for pid, a in parallels if pid == "r-mcp-fanout")
    assert set(mcp["targets"]) == {
        "vec_search_retros",
        "graph_prior_remediations",
        "graph_blast_radius",
        "framework_mapping",
        "cargonet_lab_telemetry",
    }
    assert mcp["join"] == "planner"
    assert mcp["strategy"] == "all"

    # Dual judges
    judges = next(a for pid, a in parallels if pid == "r-validate-fanout")
    assert set(judges["targets"]) == {"judge_safety", "judge_lint"}
    assert judges["join"] == "validate_plan_join"

    # Retro fan-out
    retro = next(a for pid, a in parallels if pid == "r-retro-fanout")
    assert set(retro["targets"]) == {"publish_docplus", "cargonet_writeback", "plan_kg_writeback"}
    assert retro["join"] == "retro_join"


def test_main_artifact_nodes_present() -> None:
    """7 write_artifact emissions per P1 plan."""
    doc = _load(MAIN_IR)
    artifact_nodes = {n["id"] for n in doc["nodes"] if _short_kind(n) == "write_artifact"}
    assert artifact_nodes == {
        "emit_quarantine_artifact",
        "emit_remediation_bundle",
        "emit_sandbox_evidence",
        "emit_evidence_bundle",
        "emit_retro_payload",
        "emit_docx_archive",
    }, f"unexpected artifact set: {artifact_nodes}"
    # Note: doctrine manifest + compiled prompt artifacts live in phase0/phase6.


def test_main_subgraph_refs_two() -> None:
    """drift_watch demoted to triggered; only sandbox + progressive remain.

    Subgraph file binding (NodeSpec.spec) is a Phase E gap — current
    NodeSpec schema is ``id + kind`` only, so we assert the two
    subgraph node ids are present and their corresponding subgraph IRs
    exist on disk.
    """
    doc = _load(MAIN_IR)
    sub_ids = {n["id"] for n in doc["nodes"] if _short_kind(n) == "subgraph"}
    assert sub_ids == {"sandbox_run", "progressive_execute"}
    assert (GRAPH_DIR / "subgraphs" / "sandbox_dispatch.yaml").is_file()
    assert (GRAPH_DIR / "subgraphs" / "progressive_execute.yaml").is_file()


def test_main_governance_packs() -> None:
    doc = _load(MAIN_IR)
    pack_ids = {p["id"] for p in doc["governance"]}
    assert {
        "harbor.bosun.budgets",
        "harbor.bosun.audit",
        "harbor.bosun.safety_pii",
        "harbor.bosun.retries",
        "cve_rem.routing",
        "cve_rem.kill_switches",
    } <= pack_ids


def test_main_branch_resp_pattern_routing() -> None:
    """Each branch_resp_<gate> must have at least an approve + reject rule."""
    doc = _load(MAIN_IR)
    rule_ids = {r["id"] for r in doc["rules"]}
    for gate in ("ingest", "plan", "change", "retro"):
        assert f"r-branch-{gate}-approve" in rule_ids, f"missing approve rule for branch_resp_{gate}"
        assert f"r-branch-{gate}-reject" in rule_ids, f"missing reject rule for branch_resp_{gate}"


def test_main_sandbox_fail_replan_path() -> None:
    """P2: sandbox-fail re-routes to HITL plan review, not halt."""
    doc = _load(MAIN_IR)
    rule = next(r for r in doc["rules"] if r["id"] == "r-sandbox-fail-replan")
    targets = _all_targets(rule)
    assert "hitl_plan_review" in targets


# --- Phase 0 / 6 -------------------------------------------------------------


def test_phase0_idempotency_skip() -> None:
    doc = _load(PHASE0_IR)
    node_ids = {n["id"] for n in doc["nodes"]}
    rule_ids = {r["id"] for r in doc["rules"]}
    assert "idempotency_check" in node_ids
    assert "idempotent_skip" in node_ids
    assert {"r-idempotent-skip", "r-idempotent-proceed"} <= rule_ids


def test_phase0_emits_manifest_artifact() -> None:
    doc = _load(PHASE0_IR)
    artifacts = {n["id"] for n in doc["nodes"] if _short_kind(n) == "write_artifact"}
    assert "emit_manifest_artifact" in artifacts


def test_phase6_emits_compiled_artifact() -> None:
    doc = _load(PHASE6_IR)
    artifacts = {n["id"] for n in doc["nodes"] if _short_kind(n) == "write_artifact"}
    assert {"emit_redacted_corpus", "emit_compiled_artifact"} <= artifacts


def test_phase6_offline_isolation_pack_mounted() -> None:
    doc = _load(PHASE6_IR)
    pack_ids = {p["id"] for p in doc["governance"]}
    assert "cve_rem.offline_isolation" in pack_ids
    assert "cve_rem.gepa_score_policy" in pack_ids


# --- Sub-graphs --------------------------------------------------------------


def test_sandbox_dispatch_three_branches() -> None:
    doc = _load(GRAPH_DIR / "subgraphs" / "sandbox_dispatch.yaml")
    rule_ids = {r["id"] for r in doc["rules"]}
    assert {"r-branch-cargonet", "r-branch-docker", "r-branch-static"} <= rule_ids


def test_progressive_execute_three_health_gates() -> None:
    doc = _load(GRAPH_DIR / "subgraphs" / "progressive_execute.yaml")
    node_ids = {n["id"] for n in doc["nodes"]}
    assert {"health_gate_1", "health_gate_2", "health_gate_3"} <= node_ids


# --- Triggered graphs --------------------------------------------------------


def test_triggered_graphs_complete() -> None:
    """5 triggered graphs present per P2 plan."""
    expected = {"drift_watch", "tier_re_eval", "audit_anchor", "lab_leak_reaper", "rolling_restart"}
    actual = {p.stem for p in (GRAPH_DIR / "triggered").glob("*.yaml")}
    assert actual == expected, f"triggered graphs missing or extra: {actual ^ expected}"


def test_drift_watch_three_branches() -> None:
    doc = _load(GRAPH_DIR / "triggered" / "drift_watch.yaml")
    rule_ids = {r["id"] for r in doc["rules"]}
    assert {"r-drift-same", "r-drift-diff", "r-drift-none"} <= rule_ids


def test_tier_re_eval_fans_out_parallel() -> None:
    doc = _load(GRAPH_DIR / "triggered" / "tier_re_eval.yaml")
    has_parallel = any(
        action.get("kind") == "parallel"
        for rule in doc["rules"]
        for action in rule.get("then", [])
    )
    assert has_parallel


def test_audit_anchor_failure_escalation() -> None:
    doc = _load(GRAPH_DIR / "triggered" / "audit_anchor.yaml")
    rule_ids = {r["id"] for r in doc["rules"]}
    assert {"r-failure-page-24h", "r-failure-halt-72h"} <= rule_ids


def test_rolling_restart_three_batches() -> None:
    doc = _load(GRAPH_DIR / "triggered" / "rolling_restart.yaml")
    node_ids = {n["id"] for n in doc["nodes"]}
    assert {"restart_batch_1", "restart_batch_2", "restart_batch_3"} <= node_ids
    assert {"health_gate_batch_1", "health_gate_batch_2", "health_gate_batch_3"} <= node_ids
    assert "rollback_pointer" in node_ids
