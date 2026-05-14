# SPDX-License-Identifier: Apache-2.0
"""cve_remediation run-state schemas.

One Pydantic class per graph that the runtime constructs at run-start.
Each is *flat* at the top level (Harbor's field-merge registry keys on
top-level attribute names — sub-models as VALUES are fine, but the
attribute itself is the merge key).

Pulled in via ``state_class:`` in every IR YAML, e.g.::

    state_class: "demos.cve_remediation.graph.state:CveRemState"

State classes:
- ``BaseRunState``         — common fields shared by every graph
- ``CveRemState``          — main pipeline + Phase 0 + Phase 6 + 2 sub-graphs
- ``DriftWatchState``      — triggered drift_watch
- ``TierReEvalState``      — triggered tier_re_eval
- ``AuditAnchorState``     — triggered audit_anchor
- ``LabLeakReaperState``   — triggered lab_leak_reaper
- ``RollingRestartState``  — triggered rolling_restart
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums (StrEnum so wire form stays plain string)
# ---------------------------------------------------------------------------


class SourceTrust(StrEnum):
    TRUSTED = "trusted"
    SEMI = "semi"
    UNTRUSTED = "untrusted"


class SsvcTier(StrEnum):
    ACT_AUTO = "act_auto"
    ACT_HITL_REQUIRED = "act_hitl_required"
    ATTEND = "attend"
    TRACK = "track"
    DEFER = "defer"


class SandboxRuntime(StrEnum):
    CARGONET_LAB = "cargonet_lab"
    DOCKER_COMPOSE = "docker_compose"
    STATIC_DETECTION = "static_detection"
    SKIP = "skip"


class CodeRuntime(StrEnum):
    ANSIBLE = "ansible"
    K8S = "k8s"
    TERRAFORM = "terraform"
    VENDOR_CLI = "vendor_cli"
    IMAGE_BUMP = "container_image_bump"


class TriggerKind(StrEnum):
    MANUAL = "manual"
    CRON = "cron"
    WEBHOOK = "webhook"


# ---------------------------------------------------------------------------
# Sub-models (carried as VALUES under flat top-level attrs)
# ---------------------------------------------------------------------------


class CveExtract(BaseModel):
    cve_id: str = ""
    cwe_class: str = ""
    vuln_class: str = ""
    affected_products: list[str] = Field(default_factory=list)
    affected_versions: list[str] = Field(default_factory=list)
    # NVD CPE 2.3 URIs from configurations[].nodes[].cpeMatch[].criteria.
    # Drives substrate applicability via
    # cmdb_substrate.derive_substrate_profile_from_cpes(). Source of
    # truth for "does this CVE affect a host class the fleet hosts" —
    # replaces hand-authored (vendor, product) substrate rules.
    cpe_uris: list[str] = Field(default_factory=list)
    # FR-4: state-schema floats forbidden in hashed payload — scores are
    # stored as int basis-points (cvss x 100, epss x 10000) and divided
    # at presentation time.
    cvss_score_bp: int | None = None
    epss_score_bp: int | None = None
    kev_listed: bool = False
    references: list[str] = Field(default_factory=list)


class CorrelatedAssets(BaseModel):
    affected_assets: list[str] = Field(default_factory=list)
    cmdb_match_set: list[str] = Field(default_factory=list)
    nautobot_match_set: list[str] = Field(default_factory=list)
    reconciliation_anomaly: bool = False
    blast_radius_node_count: int = 0
    disposition: str = "applicable"


class RemediationBundle(BaseModel):
    runtime: CodeRuntime = CodeRuntime.ANSIBLE
    apply_bundle_ref: str = ""
    rollback_bundle_ref: str = ""
    verify_probe_ref: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationAction(BaseModel):
    """A single discovered remediation action for a CVE.

    Sourced from one of: NVD/OSV references (``advisory_ref``), package
    registry latest-version check (``registry``), DuckDuckGo HTML
    search (``ddg_search``), local SearXNG (``searxng``).

    The pipeline auto-applies actions where ``kind in {upgrade,
    downgrade}`` AND ``confidence_bp`` exceeds the auto-apply threshold;
    every other ``kind`` surfaces to HITL with the structured payload
    + citation_url so the operator has actionable guidance instead of a
    generic "no fix published" stall.

    No fabricated actions: every entry MUST have a non-empty
    ``citation_url`` from one of the sources above. Actions emitted
    without a citation are dropped at the discovery boundary.
    """

    kind: str = ""                # upgrade | downgrade | env_var |
                                  # config_change | network_policy |
                                  # package_replace | disable_feature |
                                  # mitigation | mitigation_only |
                                  # isolate | disable | quarantine
                                  #
                                  # Phase B (2026-05-11): isolate/disable/
                                  # quarantine are first-class apply paths
                                  # for no-upstream-fix CVEs. They map to
                                  # universal infrastructure primitives
                                  # (service stop, package hold, port
                                  # block, file remove) via
                                  # ``tools/probe_primitives.py`` and run
                                  # the same apply+verify+rollback contract
                                  # as upgrade/downgrade.
    target: str = ""              # what changes (package@version,
                                  # env-var name, config file path,
                                  # service:port, etc.)
    target_version: str = ""      # for upgrade / downgrade only: the
                                  # exact version string to install.
                                  # Empty for non-version actions.
                                  # Used by SandboxRunNode to drive the
                                  # 4-step probe spec.
    change: str = ""              # the actual change directive
                                  # ("set FRED=0", "yum downgrade
                                  # xz-5.4.6", "block tcp/8000")
    rationale: str = ""           # 1-2 sentence why
    citation_url: str = ""        # required: source the action came from
    citation_excerpt: str = ""    # short verbatim quote (<=240 chars)
                                  # supporting the action
    source: str = ""              # advisory_ref | registry | ddg_search
                                  # | searxng | lm_synthesis (rejected)
    confidence_bp: int = 0        # 0..10000; auto-apply gate threshold
                                  # is ``CVE_REM_AUTO_APPLY_BP``


class RecommendationProvenance(BaseModel):
    """Per-source diagnostics for the discovery run."""

    sources_attempted: list[str] = Field(default_factory=list)
    sources_succeeded: list[str] = Field(default_factory=list)
    references_fetched: int = 0
    search_results_fetched: int = 0
    registry_check_result: str = ""    # e.g. "pip:latest=42.0.4 unaffected"
    lm_actions_emitted: int = 0
    lm_actions_dropped_no_citation: int = 0
    last_error: str = ""


class RetroFailureSignal(BaseModel):
    """One observable failure signal detected on the run state.

    Detector reads only observable state fields (no LM); the signal
    carries the field names + values that fired so the downstream LM
    analyzer cannot fabricate citations -- it must reference these
    exact observed values when proposing a prevention suggestion.
    """

    kind: str = ""              # verify_unpatched | sandbox_quarantined |
                                # sandbox_skipped | rollback |
                                # no_fix_published | planner_error |
                                # sandbox_error | correlation_error |
                                # host_verify_failed | verifier_finding |
                                # capability_violation | divergence
    detail: str = ""            # short human-readable description
    evidence: dict[str, Any] = Field(default_factory=dict)
                                # {field_name: observed_value} pairs
                                # the LM must cite verbatim


class PreventionSuggestion(BaseModel):
    """One LM-synthesized fix / prevention proposal tied to one or more
    failure signals.

    Cited evidence is the same shape the discovery node uses: every
    suggestion MUST cite at least one observed signal field name from
    the failure_signals list.  Suggestions without citations are
    dropped by the analyzer.
    """

    category: str = ""          # pipeline | advisory_data | sandbox |
                                # planner | dispatch | hitl |
                                # infrastructure | retrospective_data
    suggestion: str = ""        # concrete actionable change
    rationale: str = ""         # 1-2 sentences why this fixes /
                                # reduces recurrence
    cited_signals: list[str] = Field(default_factory=list)
                                # failure-signal kinds this suggestion
                                # is grounded on (e.g. ["planner_error",
                                # "no_fix_published"])
    confidence_bp: int = 0      # 0..10000 -- how sure the analyzer is
                                # this prevents recurrence
    citation_url: str = ""      # optional external citation (advisory,
                                # docs, file:line) when the suggestion
                                # references off-state knowledge


class SandboxResult(BaseModel):
    runtime: SandboxRuntime = SandboxRuntime.SKIP
    status: str = "pending"
    baseline_probe: str = ""
    apply_probe: str = ""
    rollback_probe: str = ""
    reapply_probe: str = ""
    skip_reason: str = ""
    force_hitl: bool = False


class CriticVerdict(BaseModel):
    verdict: str = "feedback"
    feedback_text: str = ""
    veto_flags: list[str] = Field(default_factory=list)
    attempt: int = 1


class HitlGate(BaseModel):
    name: str = ""
    triggered: bool = False
    waiting_since: datetime | None = None
    backup_notify_at: datetime | None = None
    decision: str = ""
    decided_by: str = ""


class Attestations(BaseModel):
    fathom_jws_ids: list[str] = Field(default_factory=list)
    nautilus_jws_ids: list[str] = Field(default_factory=list)
    llm_session_attestations: list[str] = Field(default_factory=list)
    prompt_artifact_id: str = ""
    doctrine_manifest_hash: str = ""


class HitlResponse(BaseModel):
    """Shape of analyst response patched in by GraphRun.respond."""

    decision: Literal["approve", "reject", "approve_replan", "escalate"]
    actor: str
    note: str = ""
    at: datetime


# ---------------------------------------------------------------------------
# Base + main
# ---------------------------------------------------------------------------


class BaseRunState(BaseModel):
    """Fields every cve_remediation graph carries."""

    run_id: str = ""
    parent_run_id: str = ""
    trigger_kind: TriggerKind = TriggerKind.MANUAL
    halt_reason: str = ""

    # Provenance envelopes patched by tool-shaped nodes (Harbor mirror
    # framework reads from ``harbor_provenance__`` — leading dunder
    # disallowed by Pydantic field naming rules).
    harbor_provenance__: dict[str, Any] = Field(default_factory=dict)


class CveRemState(BaseRunState):
    """Main pipeline state — covers Phase 0..5 + sub-graphs.

    Flat at the top level so Harbor's field-merge registry can route
    every node's output to a single attribute.
    """

    # --- Phase 1 intake ---
    raw_source_url: str = ""
    raw_source_body: str = ""  # raw fetched advisory body (markdown / HTML / text)
    last_intake_error: str = ""  # IntakeFetchNode failure surface (empty on success)
    cve_vendor: str = ""  # NVD CPE-derived; used by CMDB lookup in Phase 2
    cve_product: str = ""  # NVD CPE-derived; used by CMDB lookup in Phase 2
    # Full candidate-product list (NVD CPE products + description fallback,
    # in priority order) — CMDB correlation iterates this until one product
    # matches a Software CI in the live PDI. Survives multi-vendor CVEs
    # where ``cpeMatch[0]`` is the wrong package.
    candidate_products: list[str] = Field(default_factory=list)
    # The exact NVD CPE product token that the CMDB correlation hit --
    # preserved separately from ``cve_product`` (which gets overwritten
    # with the human-friendly CMDB display name). The sandbox probe
    # uses this for install commands because registry-canonical names
    # like ``log4j-core`` rarely match human display strings like
    # ``Apache Log4j 2``.
    matched_candidate_product: str = ""
    # Advisory-derived version + channel signals -- consumed by the
    # sandbox probe to pick install specs WITHOUT per-CVE hardcoding.
    # ``fixed_version`` = first NVD ``versionEndExcluding`` for the
    # primary product. ``exact_affected_versions`` = literal versions
    # parsed from CPE 2.3 ``criteria`` segment. ``install_channel`` =
    # package registry inferred from advisory references (pip / maven
    # / apt / rpm / npm / rubygems / cargo / go / github).
    fixed_version: str = ""
    exact_affected_versions: list[str] = Field(default_factory=list)
    affected_version_ranges: list[dict[str, str]] = Field(default_factory=list)
    install_channel: str = ""
    # OSV-canonical package name (e.g. ``org.apache.logging.log4j:log4j-core``
    # for Maven, ``pillow`` for PyPI). The sandbox probe uses this for
    # registry-aware install commands; it carries the full ecosystem
    # coord that CMDB-matched display names lack.
    osv_package_name: str = ""
    # Advisory-level remediation status. ``""`` = normal patch path
    # (probe runs; ``fix_version`` is the upgrade target).
    # ``"withdrawn"`` = OSV ``withdrawn`` timestamp on the advisory.
    # ``"no_fix_published"`` = OSV has affected entries but no
    # ``fixed`` event anywhere (e.g. CVE-2024-3094: maintainers pulled
    # the tarballs rather than ship a patch). The sandbox layer routes
    # both to mitigation_only HITL with an explicit reason.
    vulnerability_status: str = ""
    advisory_references: list[dict[str, Any]] = Field(default_factory=list)
    # NVD CPE 2.3 URIs from the advisory (configurations[].nodes[].cpeMatch[].criteria).
    # Surfaced by IntakeFetchNode; consumed by the substrate guard via
    # cmdb_substrate.derive_substrate_profile_from_cpes(). Empty list ⇒
    # substrate guard fails open (no CPE data ≠ ineligible).
    advisory_cpe_uris: list[str] = Field(default_factory=list)
    # Discovered remediation actions (RemediationDiscoveryNode output).
    # Auto-applied for ``upgrade`` / ``downgrade`` kinds when confidence
    # exceeds threshold; every other kind surfaces to HITL with
    # structured payload + citation. ``recommendation_provenance``
    # carries per-source diagnostics for auditor inspection.
    recommended_actions: list[RemediationAction] = Field(default_factory=list)
    recommendation_provenance: RecommendationProvenance = Field(
        default_factory=RecommendationProvenance,
    )
    cmdb_query_count: int = 0  # rows returned by live ServiceNow CMDB query
    last_cmdb_error: str = ""  # CMDB lookup failure surface
    cmdb_software_sys_id: str = ""  # parent Software CI sys_id from cmdb_ci_spkg
    cmdb_software_name: str = ""  # exact name field on the Software CI
    # Correlation hardening (2026-05-08): composite confidence score for
    # the CMDB match (vendor + token coverage + catch-all penalty).
    # Surfaces in scoring artifacts so reviewers can split substring-noise
    # hits from high-confidence ones.
    cmdb_match_score: int = 0
    cmdb_match_quality: Literal[
        "", "high", "medium", "low", "reject", "miss",
        "low_conf_no_topo", "version_excluded", "substrate_denied",
    ] = ""
    # Phase F+ (2026-05-11): Nautilus-aligned substrate audit. When the
    # cve_rem.cmdb_substrate_guard rule pack (or its python mirror) drops
    # one or more hosts from the CMDB result, the per-host decisions are
    # captured here so the evidence bundle + CR work_notes can show why
    # the substrate guard rejected a host (e.g. "Apache Log4j cannot run
    # on a db-role host"). Empty when no CMDB call happened.
    substrate_filter: dict[str, Any] = Field(default_factory=dict)
    # Phase G (2026-05-12): per-lead audit chain emitted by
    # CorrelateAgent. One entry per (vendor, product) pair drawn from
    # the advisory's CPE 2.3 URI list, recording variants tried,
    # scored candidates, matched Software CI, and surfaced hosts.
    correlate_agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    # Phase E (2026-05-11): version-range gate observability.
    # cmdb_ci_version = the matched Software CI's installed version
    # (empty when CMDB seed lacks the field). cmdb_version_gate_status
    # ∈ {"", "in_range", "out_of_range", "unknown"}; "unknown" means
    # gate was no-op (CI version missing OR advisory ranges empty).
    cmdb_ci_version: str = ""
    cmdb_version_gate_status: Literal[
        "", "in_range", "out_of_range", "unknown",
    ] = ""
    # Phase C (2026-05-11): honest unpatchable terminal.
    # Set when advisory has no upstream fix AND no IoC-driven primitive
    # action was synthesizable AND the CVE has host coverage in the
    # environment. Routes the run through HITL change approval with a
    # structured "isolate or disable until vendor patches" payload —
    # NEVER fakes fleet_passed=True. The operator confirms the chosen
    # disposition; on approve, the run records the decision as the
    # CRITERIA-passing outcome for an unpatchable CVE.
    #
    #   isolate_recommended  — network-segment / firewall isolation
    #                          recommended (severity below high OR
    #                          host-class amenable to network gating)
    #   disable_recommended  — service / package disable recommended
    #                          (severity ≥ high OR KEV listed)
    unpatchable_disposition: Literal[
        "", "isolate_recommended", "disable_recommended",
    ] = ""
    unpatchable_reason: str = ""  # brief operator-facing rationale
    affected_host_names: list[str] = Field(default_factory=list)  # host CI names traversed via Runs-on
    cargonet_lab_ref: str = ""  # lab id where the affected product is provisioned
    cargonet_proxy_ref: list[str] = Field(default_factory=list)  # CargoNet node ids matching the CVE product
    cargonet_node_count: int = 0
    cargonet_correlation_map: dict[str, dict[str, str]] = Field(default_factory=dict)  # host_name -> {lab_id, node_id}
    last_cargonet_error: str = ""
    canonical_body: str = ""   # NFKC-normalized, markdown-stripped
    source_trust: SourceTrust = SourceTrust.UNTRUSTED
    untrusted_text_suspected: bool = False
    untrusted_text_influenced: bool = False
    extract: CveExtract = Field(default_factory=CveExtract)
    cve_id: str = ""
    cwe_class: str = ""
    vuln_class: str = ""
    injection_class: Literal["", "clean", "suspicious", "attack_pattern"] = ""
    canonicalization_quarantine_id: str = ""
    quarantine_artifact_ref: str = ""

    # --- Phase 2 correlate + tier ---
    correlated: CorrelatedAssets = Field(default_factory=CorrelatedAssets)
    disposition: Literal["applicable", "not_applicable"] = "applicable"
    ssvc_tier: SsvcTier = SsvcTier.ATTEND

    # --- Phase 3 plan + sandbox ---
    template_lookup_hit: bool = False
    template_lookup_miss_reason: str = ""
    plan_hash: str = ""
    plan_rationale: str = ""
    planner_latency_ms: int = 0
    last_planner_error: str = ""
    # Task #83 -- multi-turn agent trace. Each step is
    # ``{role, content}`` dict so the auditor can see exactly which
    # tools the planner called and what they returned.
    planner_agent_trace: list[dict[str, str]] = Field(default_factory=list)
    # Bounded JSON-schema retry count for FINAL plan-shape validation
    # in _call_planner_agent_multi_turn (cap = _PLANNER_SCHEMA_MAX_RETRIES).
    planner_schema_retries: int = 0
    # Tier-1 deterministic hallucination guard: each finding is a
    # ``{kind, claim, expected}`` dict. Empty list ⇒ rationale passed
    # the cross-check. ``planner_verifier_passed`` is the routable
    # boolean for downstream rules (HITL escalate on False).
    planner_verifier_findings: list[dict[str, str]] = Field(default_factory=list)
    planner_verifier_passed: bool = True
    # Tier-2 RAG grounding: list of source URLs retrieved + injected
    # into the planner prompt; the rationale is expected to cite from
    # this set. Citations the LM emits as ``[CITE: <url>]`` are parsed
    # back out and validated against the corresponding source body.
    planner_rag_sources: list[dict[str, str]] = Field(default_factory=list)
    planner_citation_findings: list[dict[str, str]] = Field(default_factory=list)
    code_runtime: CodeRuntime = CodeRuntime.ANSIBLE
    # Phase F (2026-05-11): structured 4-tuple plan (apply / verify /
    # rollback / regression) derived deterministically from
    # recommended_actions + extract. CodeWriterNode consumes this when
    # complete and bypasses the LM bundle path entirely (cuts rollback
    # variance). Empty default = LM bundle path. ``plan_spec_deficits``
    # mirrors ``critic_deficits`` shape so a planner-retry can read both
    # the planner's self-detected gaps and the critic's findings.
    plan_spec: dict[str, Any] = Field(default_factory=dict)
    plan_spec_deficits: list[dict[str, str]] = Field(default_factory=list)
    bundle: RemediationBundle = Field(default_factory=RemediationBundle)
    remediation_bundle_artifact_ref: str = ""
    critic_history: list[CriticVerdict] = Field(default_factory=list)
    critic_attempt: int = 0
    critic_verdict: Literal["", "approved", "feedback", "veto"] = ""
    # Phase F: structured deficits the critic surfaces alongside the
    # legacy text feedback. Each: {kind, slot, detail}.
    critic_deficits: list[dict[str, str]] = Field(default_factory=list)
    judge_safety_verdict: Literal["", "pass", "fail"] = ""
    judge_lint_verdict: Literal["", "pass", "fail"] = ""
    sandbox_runtime: SandboxRuntime = SandboxRuntime.SKIP
    sandbox: SandboxResult = Field(default_factory=SandboxResult)
    sandbox_status: Literal["pending", "ok", "fail", "skipped", "quarantined"] = "pending"
    # Per-phase probe metadata: ``{phase: {uri, digest, status, spec,
    # latency_ms}}``. ``status`` here is the *observed* outcome
    # (vulnerable / patched / error), not the planned label — the gap
    # between observed and expected drives quarantine in
    # SandboxRunNode (CRITERIA fancy #4).
    sandbox_probe_steps: dict[str, Any] = Field(default_factory=dict)
    # CRITERIA fancy #4: any phase whose observed status diverges from
    # the expected polarity sets this. Downstream rules block apply
    # and force HITL.
    sandbox_quarantined: bool = False
    sandbox_quarantine_reason: str = ""
    # Suggestion #3 retry: list of {attempt, quarantined, reason} dicts
    # populated when SandboxRunNode retries after quarantine.  Bounded
    # by MAX_SANDBOX_RETRIES; surfaces in retro analysis as evidence.
    sandbox_retry_attempts: list[dict[str, Any]] = Field(default_factory=list)
    # Suggestion #4 STATIC_DETECTION probe: per-host pip-show / rpm-q
    # results.  One row per host in ``affected_host_names``.
    static_detection_per_host: list[dict[str, Any]] = Field(default_factory=list)
    # Retro round #A/B/C/D: mitigation-only mode for advisories with
    # ``vulnerability_status="withdrawn"`` (or ``no_fix_published``)
    # AND a non-empty mitigation set from RemediationDiscoveryNode.
    # When True:
    #   * ProgressiveExecuteNode does NOT run canary/stage/fleet apply
    #     (no upstream patch to install) — records mitigations only.
    #   * VerifyImmediateNode emits ``verify_outcome="mitigation_verified"``
    #     instead of "vulnerable" (vuln is the *expected* state under
    #     mitigation; reduction-of-exposure is the goal).
    #   * WriteRetrospectiveNode maps that to retro_outcome="mitigation_applied"
    #     so cross-run learning sees these as bounded successes, not rollbacks.
    mitigation_only: bool = False
    # Mitigation-validation probe: ProgressiveExecuteNode short-circuits
    # apply on the mitigation_only path; the structural probe asserts
    # each mitigation action has target/change/citation/confidence so
    # ``mitigation_applied`` reflects validated guidance, not just a
    # claim. Probe-failed runs collapse to ``mitigation_invalid``
    # outcome and emit a retro failure signal.
    mitigation_probe_passed: bool = False
    mitigation_probe_issues: list[str] = Field(default_factory=list)
    # Retro round #E: tally of consecutive verify-vulnerable observations
    # within the same run.  Two-strike rollback gate: rollback fires only
    # when verify_attempts >= 2 AND last result was vulnerable.  Avoids
    # transient probe-flake collapsing a run that would otherwise patch.
    verify_vulnerable_attempts: int = 0
    # Fancy CRITERIA #4: set True when ProgressiveExecuteNode halts
    # due to sandbox quarantine. Routable to a Fathom rule / external
    # alerting listener. Recorded as ``[oncall-page]`` work-note on CR.
    oncall_paged: bool = False
    # Fancy CRITERIA #5: sandbox-prod divergence detection +
    # plan-KG quarantine. ``plan_quarantined`` is set by the
    # PlanQuarantineGateNode when the run's plan_hash is already
    # listed in ``cve_rem_plan_quarantine`` (a prior run hit
    # divergence). The gate halts the run before sandbox runs.
    plan_quarantined: bool = False
    plan_quarantine_reason: str = ""
    # Fancy CRITERIA #12: pipeline-level outcome capture + halt-new
    # gate. ``run_outcome_written`` is True when this run wrote a row
    # to ``cve_rem_run_outcomes``. ``halt_new_active`` is set by
    # HaltNewGateNode at pipeline start when a fleet-wide halt-new
    # ledger entry is live within the TTL window.
    run_outcome_written: bool = False
    last_run_outcome_error: str = ""
    halt_new_active: bool = False
    # Fancy CRITERIA #5: GEPA receives the divergence record. Surface
    # the row id (or "" when no divergence happened) so an external
    # listener can join cve_rem_gepa_divergence to other GEPA tables.
    gepa_divergence_record_id: str = ""
    sandbox_probe_latency_ms: int = 0  # total wall across the 4-step probe
    last_sandbox_error: str = ""
    skip_sandbox: bool = False
    sandbox_evidence_artifact_ref: str = ""
    validation_passed: bool = False

    # --- Phase 4 CR + execute + verify ---
    cr_correlation_id: str = ""
    cr_status: Literal["", "draft", "approved", "rejected", "implemented"] = ""
    # Raw envelope from harbor.tools.servicenow.create_change_request.
    # Carries either a dry-run body (default) or the live ServiceNow API
    # response under ``result``. Replay-policy-relevant: WriteArtifactNode
    # cassettes do not cover this field; live mode is non-deterministic
    # by definition.
    servicenow_response: dict[str, Any] = Field(default_factory=dict)
    cr_request_body: dict[str, Any] = Field(default_factory=dict)  # the body posted to /change_request
    task_ci_link_count: int = 0  # additional affected CIs linked via task_ci table
    change_task_count: int = 0  # change_task child rows POSTed for each cargonet proxy node
    last_cr_link_error: str = ""
    cr_lifecycle_states: list[str] = Field(default_factory=list)  # ordered state transitions advanced post-create
    last_cr_lifecycle_error: str = ""
    # Step 7 H: track whether business_service / service_offering came
    # from a live SN cmdb_ci_service lookup, an env override, or
    # nothing (i.e., the CR will land with empty service fields). The
    # verify harness asserts this is one of resolved_live /
    # resolved_env so the CR self-validates against
    # the criterion "all required spec fields populated".
    cr_service_lookup_status: Literal[
        "", "resolved_live", "resolved_env", "missing", "sn_unreachable"
    ] = ""
    hitl_blocked_at: str = ""  # gate name when HITL halted the run pending external response
    # Phase F+ (2026-05-11): True when the runtime successfully wrote a
    # row to cve_rem_hitl_persistence at block time. False when PG was
    # unconfigured / unreachable (HITL still blocks; just no durability).
    hitl_persistence_written: bool = False
    retro_pg_written: bool = False
    retro_redis_written: bool = False
    retro_pgvector_written: bool = False  # pgvector embedding written (task #70)
    retro_suggestion_count: int = 0  # LM-generated suggestion records (task #70)
    last_retro_error: str = ""
    prior_retro_count: int = 0  # rows pulled from Redis Reflexion buffer for this CWE
    prior_retro_outcomes: dict[str, int] = Field(default_factory=dict)
    # Step 10 G10: surface retrieval health so the verify harness can
    # fail loud when both stores are unreachable. ``ok``=both stores
    # answered; ``redis_only``/``pg_only``=one path failed but the
    # other returned data; ``degraded``=both stores errored.
    prior_retro_retrieval_status: Literal[
        "", "ok", "redis_only", "pg_only", "degraded"
    ] = ""
    # Phase A3: which retrieval path actually fired for the top-K
    # suggestion fetch. ``semantic_nn``=real cosine NN against current
    # CVE's embedding (the architecture intent). ``cwe_recency_fallback``
    # =legacy WHERE cwe=$1 path (embedding endpoint down). ``error``=
    # pgvector query raised. ``skipped_no_pg``=PG_DSN not configured.
    prior_retro_retrieval_mode: Literal[
        "", "semantic_nn", "cwe_recency_fallback", "error", "skipped_no_pg"
    ] = ""
    # Phase B: actions returned by GraphPriorRemediationsNode via Cypher
    # over the runtime KG (CVE/Action/Run nodes written by
    # KgRunWriterNode on previous runs). Each entry:
    # {kind, target_version, advisory_ref, lane, freq}.
    graph_prior_actions: list[dict[str, Any]] = Field(default_factory=list)
    graph_prior_retrieval_status: Literal[
        "", "ok", "empty_graph", "neo4j_creds_unset",
        "neo4j_driver_missing", "error", "no_query_input"
    ] = ""
    last_graph_prior_error: str = ""
    # Phase B: KgRunWriterNode telemetry.
    kg_run_written: bool = False
    kg_run_nodes_written: int = 0
    kg_run_edges_written: int = 0
    last_kg_run_error: str = ""
    # Doctrine fallback (FrameworkMappingNode): NIST 800-53 Controls and
    # CAPEC attack patterns mapped from the current CVE's CWE. Surfaced
    # in CR description + Doc+ doc body so sandbox-skipped CVEs
    # (firmware/embedded) carry compensating-control guidance instead of
    # just "skipped". Each control: {id, name}. Each pattern: {id, name}.
    framework_controls: list[dict[str, str]] = Field(default_factory=list)
    attack_patterns: list[dict[str, str]] = Field(default_factory=list)
    framework_mapping_status: Literal[
        "", "ok", "empty", "no_cwe", "neo4j_creds_unset",
        "neo4j_driver_missing", "error"
    ] = ""
    last_framework_mapping_error: str = ""
    prior_retros_pg_count: int = 0  # cve_rem_retros rows for the same CWE
    prior_retros_pg_last_seen: str = ""  # MAX(written_at) ISO; empty when unknown
    # Step 12 (b): top-K rows from cve_rem_retro_suggestions joined to
    # cve_rem_retro_embeddings filtered to the same CWE. Each entry:
    # ``{retro_id, suggestion_text, generated_at}``. Read by PlannerNode
    # and injected into the rationale + LM prompt so Run N's plan
    # genuinely incorporates lessons from Runs 1..N-1 (not just a flag).
    prior_retro_suggestions: list[dict[str, str]] = Field(default_factory=list)
    # PlannerNode bumps this when it injects suggestions into the
    # rationale. The verifier asserts Run 2 > Run 1 so we have proof
    # the planner consumed something Run 1 wrote.
    suggestions_consumed_count: int = 0
    # Step 12 (d'): substantive growth metric. Composite int basis-
    # point score (0..10000) computed by PlannerNode from
    # suggestions_consumed_count, prior_retro_count, and inverse
    # planner_verifier_findings. Run 2 > Run 1 demonstrates that the
    # planner's output got better when prior retros were available.
    plan_quality_score_bp: int = 0
    last_reflexion_error: str = ""
    evidence_bundle_artifact_ref: str = ""
    canary_passed: bool = False
    stage_passed: bool = False
    fleet_passed: bool = False
    execution_ledger: list[str] = Field(default_factory=list)
    rollback_triggered: bool = False
    verify_outcome: Literal[
        "", "patched", "vulnerable", "divergence", "unverified",
        "mitigation_verified", "unpatchable_hitl_pending",
        "substrate_not_applicable",
    ] = ""
    drift_watch_window_hours: int = 48  # set from tier in VerifyImmediateNode (task #69)
    sandbox_prod_divergence: bool = False
    drift_events: list[str] = Field(default_factory=list)

    # --- Phase 5 retro ---
    retro_id: str = ""
    retro_outcome: Literal[
        "", "patched", "rollback", "divergence",
        "mitigation_applied", "mitigation_invalid",
        "not_applicable", "incomplete", "vulnerable", "unverified",
        "unpatchable_pending", "substrate_not_applicable",
    ] = ""
    retro_payload_artifact_ref: str = ""
    # Substantive failure analysis (set by WriteRetrospectiveNode when
    # detect_failure_signals fires on observable state). Empty for
    # clean ``patched`` runs; populated for rollback / divergence /
    # quarantine / planner-error / etc. Persisted alongside the retro
    # payload so future runs of the same CWE can read these
    # suggestions via the existing prior-retro-suggestion plumbing.
    retro_failure_signals: list[RetroFailureSignal] = Field(default_factory=list)
    retro_failure_analysis: str = ""
    retro_prevention_suggestions: list[PreventionSuggestion] = Field(default_factory=list)
    retro_analysis_error: str = ""
    docx_artifact_ref: str = ""
    docplus_published: bool = False
    docplus_staging_ref: str = ""
    docplus_attachment_sys_id: str = ""  # SN attachment sys_id (task #71)
    # Doc+ table integration: per-CVE record in x_krn_document_doc
    # linked to the 'Vulnerability Summaries' collection in
    # x_krn_document_collection via the m2m table
    # x_krn_document_m2m_x_krn_docume_x_krn_docume.
    docplus_collection_sys_id: str = ""    # collection record (one per
                                           # collection, reused per run)
    docplus_doc_sys_id: str = ""           # per-CVE doc record
    docplus_doc_attachment_sys_id: str = ""  # docx attachment on doc
                                           # record (distinct from
                                           # docplus_attachment_sys_id
                                           # which lives on the CR)
    docplus_m2m_sys_id: str = ""           # m2m row linking doc → collection
    # Per the Doc+ schema (x_krn_document_version), the file lives on a
    # version row -- not on the doc row. Doc holds the immutable handle,
    # each version row carries mutable content + lifecycle state.
    docplus_version_sys_id: str = ""           # v1 record under doc
    docplus_version_attachment_sys_id: str = ""  # DOCX bytes on version
    last_docplus_table_error: str = ""     # surface for verifier / HITL
    # EmitDocxArchiveNode surface — set when python-docx rendering fails
    # or markdown source path missing.
    last_docx_emit_error: str = ""
    # Task #79 — every artifact gets attached to the CR (Ansible bundle,
    # sandbox stdout per phase, retro DOCX, evidence bundle, manifest).
    attachment_sys_ids: list[str] = Field(default_factory=list)
    attachment_count: int = 0
    attachment_manifest: list[dict[str, str]] = Field(default_factory=list)
    last_attachment_error: str = ""
    # Task #84 -- CR self-validation: refetch the CR + every attachment
    # row and assert thresholds; refusal here halts on insufficient
    # evidence so the operator can never review a bare-bones CR.
    cr_self_validation_passed: bool = False
    cr_self_validation_findings: list[str] = Field(default_factory=list)
    cr_observed_field_lengths: dict[str, int] = Field(default_factory=dict)
    cr_observed_attachment_count: int = 0
    cr_observed_journal_count: int = 0
    last_cr_self_validation_error: str = ""
    # Task #86 -- single Markdown audit report attached to the CR.
    proof_report_artifact_ref: str = ""
    proof_report_attachment_sys_id: str = ""
    last_proof_report_error: str = ""
    cargonet_writeback_done: bool = False
    plan_kg_writeback_done: bool = False
    cmdb_match_correct: bool = True

    # --- HITL responses ---
    response: HitlResponse | None = None
    hitl_gates: dict[str, HitlGate] = Field(default_factory=dict)

    # --- Cross-cutting ---
    attestations: Attestations = Field(default_factory=Attestations)
    fact_set_watermark: str = ""
    # Fancy CRITERIA #1 — trust-chain attestation walk.
    # ``prompt_artifact_id`` is BLAKE3 over (plan_rationale, RAG
    # citations, agent trace) so the LM "prompt artifact" used for
    # the run is content-addressable and reproducible.
    # ``run_attestation_jws`` is a compact EdDSA JWS signed with the
    # krakntrust dev key over the run's chain payload (cr_sys_id,
    # prompt_artifact_id, doctrine_manifest_hash, retro_id, etc.).
    # ``boot_session_id`` = BLAKE3 of the krakntrust pubkey PEM —
    # root-of-trust anchor; rotates if the keypair rotates.
    # ``krakntrust_key_id`` = ``krakntrust-cve-rem-<8 hex>`` matching
    # the pubkey filename so the verifier can locate the pinned key.
    prompt_artifact_id: str = ""
    run_attestation_jws: str = ""
    run_attestation_artifact_ref: str = ""
    run_attestation_attachment_sys_id: str = ""
    boot_session_id: str = ""
    krakntrust_key_id: str = ""
    last_attestation_error: str = ""
    # E1 real-node observability
    last_artifact_uri: str = ""
    last_artifact_hash: str = ""
    last_artifact_written_at: str = ""
    # GEPA component values in int basis-points (E1 GepaScoreComputerNode)
    gepa_components: dict[str, int] = Field(default_factory=dict)
    # E3 broker-intent envelope (last constructed; live dispatch deferred)
    broker_request_envelope: dict[str, Any] = Field(default_factory=dict)
    last_broker_intent: str = ""
    # Source-trust audit (task #74)
    source_audit_written: bool = False
    last_source_audit_error: str = ""
    # Fancy CRITERIA #2: per-run audit fields surfaced from the audit
    # node. ``source_trust_violation`` is True when an untrusted
    # source bypassed the injection classifier — deploy-blocking.
    # ``source_classifier_ran`` is the canonical observability flag
    # ("did the classifier run on this intake?"). ``source_class`` is
    # the doctrine bucket (vendor / cna-trusted / cna-semi / news /
    # social / unknown) the URL was resolved to.
    source_trust_violation: bool = False
    source_classifier_ran: bool = False
    source_hitl_forced: bool = False
    source_class: str = ""
    # Drift child run (task #75)
    drift_child_run_id: str = ""
    last_drift_spawn_error: str = ""
    # Step 7 E: how the drift child was spawned. ``scheduler`` (live
    # in-process), ``http`` (live POST /v1/runs), or ``intent-only``
    # (deterministic id minted; no real runner reached). The verify
    # harness allows all three so the audit chain stays unbroken even
    # when a runner isn't accessible from the verify environment.
    drift_spawn_path: Literal[
        "", "scheduler", "http", "intent-only"
    ] = ""
    # Step 7 C: per-host verify probe results. Each entry shape:
    # {host, expected_version, observed_version, ok: bool,
    #  probe_method: ssh|k8s|offline-trust|none, latency_ms, error?}.
    # ``verify_outcome`` flips to "patched" only when every entry is
    # ok; otherwise verify lands at "unverified" and the CR stays
    # held at the review state for operator triage.
    per_host_verify_results: list[dict[str, Any]] = Field(default_factory=list)
    # CargoNet Phase 2: per-host install results from
    # ProgressiveExecuteNode. Each row carries host, package, channel,
    # install_command, apply_exit_code, observed_version (post-install),
    # ok, latency_ms, evidence|error. Real bytes from CargoNet exec --
    # not synthesized canary/stage/fleet booleans.
    per_host_apply_results: list[dict[str, Any]] = Field(default_factory=list)
    verify_probe_method: Literal[
        "",
        "cargonet",
        "offline-trust",
        "ssh",
        "k8s",
        "none",
        # Upstream-set values: bundle/recipe apply paths and the
        # short-circuit outcomes VerifyImmediateNode honors. Kept here
        # so direct CveRemState construction (tests, replay) accepts
        # the same values real_nodes.py emits via field-merge.
        "ansible-bundle",
        "bundle-no-verify-tasks",
        "recipe",
        "mitigation",
        "substrate",
        "unpatchable",
    ] = ""

    # --- Phase 0 doctrine ingest (reused for Phase 0 graph) ---
    corpus_version_pin: str = ""
    corpus_sha256: str = ""
    corpus_already_allowlisted: bool = False
    doctrine_node_count: int = 0
    doctrine_edge_count: int = 0
    doctrine_kg_neo4j_nodes_written: int = 0
    doctrine_kg_neo4j_edges_written: int = 0
    last_kg_loader_error: str = ""
    doctrine_manifest_hash: str = ""
    manifest_signature: str = ""
    manifest_artifact_ref: str = ""

    # --- Phase 6 offline learning (reused for Phase 6 graph) ---
    holdout_retro_count: int = 0
    redacted_corpus_hash: str = ""
    redacted_corpus_artifact_ref: str = ""
    candidate_artifact_hash: str = ""
    candidate_artifact_ref: str = ""
    current_artifact_hash: str = ""
    # GEPA scores held as int basis-points (score x 10000) — FR-4.
    candidate_score_bp: int = 0
    current_score_bp: int = 0
    epsilon_margin_bp: int = 200  # 0.02 * 10000
    strictly_better: bool = False
    shamir_quorum: Literal["", "reached", "not_reached"] = ""
    ship_audit_id: str = ""


# ---------------------------------------------------------------------------
# Triggered-graph state classes
# ---------------------------------------------------------------------------


class DriftWatchState(BaseRunState):
    cve_id: str = ""
    watch_window_hours: int = 48
    drift_detected: bool = False
    drift_signature_match: bool = False
    drift_outcome: Literal["", "spawned", "paged", "clean"] = ""
    drift_summary_artifact_ref: str = ""


class TierReEvalState(BaseRunState):
    scanned_pair_count: int = 0
    tier_escalations_count: int = 0
    tier_unchanged_count: int = 0
    spawned_run_ids: list[str] = Field(default_factory=list)
    summary_artifact_ref: str = ""


class AuditAnchorState(BaseRunState):
    chain_head_sha256: str = ""
    partition_date: str = ""
    anchor_status: Literal["", "ok", "failed"] = ""
    sustained_failure_hours: int = 0
    receipt_artifact_ref: str = ""


class LabLeakReaperState(BaseRunState):
    active_lab_count: int = 0
    expired_lab_count: int = 0
    reaped_lab_count: int = 0
    reaper_summary_artifact_ref: str = ""


class RollingRestartState(BaseRunState):
    artifact_id: str = ""
    previous_artifact_id: str = ""
    batch_1_ok: bool = False
    batch_2_ok: bool = False
    batch_3_ok: bool = False
    rollback_triggered: bool = False
    restart_summary_artifact_ref: str = ""


__all__ = [
    "Attestations",
    "AuditAnchorState",
    "BaseRunState",
    "CodeRuntime",
    "CorrelatedAssets",
    "CriticVerdict",
    "CveExtract",
    "CveRemState",
    "DriftWatchState",
    "HitlGate",
    "HitlResponse",
    "LabLeakReaperState",
    "RemediationBundle",
    "RollingRestartState",
    "SandboxResult",
    "SandboxRuntime",
    "SourceTrust",
    "SsvcTier",
    "TierReEvalState",
    "TriggerKind",
]
