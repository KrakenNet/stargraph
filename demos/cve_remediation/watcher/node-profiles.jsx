// node-profiles.jsx — hand-authored domain profile for every cve-rem node.
//
// Each entry describes what the node does, what it reads, what it writes,
// what capabilities it needs, and the on-disk / on-graph artifacts that
// constitute its evidence. The LiveNodeView dispatcher renders a rich panel
// using these fields plus the IR config + captured runtime events.

const NODE_PROFILE = {
  // ─── Gating ─────────────────────────────────────────────────────────────
  halt_new_gate: {
    family: "gate",
    title: "Halt-new gate",
    role: "Fleet-wide kill switch. Refuses to start a new remediation run when the rolling rollback-rate exceeds the F12 threshold.",
    inputs: ["fleet_outcomes(window=24h)", "F12 threshold (default 0.20)"],
    outputs: ["passthrough on green", "{halted:true} → halt action"],
    side_effects: "read-only Postgres lookup",
    capabilities: ["read:fleet_outcomes"],
    evidence: ["fathom_pack/fleet_halt_new.clp", "run_outcome_persist writes inputs to this gate"],
    cite: "F12 hardening — kraken.fleet_outcomes",
  },

  // ─── Source ingest ──────────────────────────────────────────────────────
  intake_fetch: {
    family: "source",
    title: "NVD intake fetch",
    role: "Pulls the canonical CVE record + EPSS percentile + CISA KEV listing for the input cve_id.",
    inputs: ["cve_id (CVE-YYYY-NNNN)"],
    outputs: ["state.cve_record", "state.epss_score", "state.kev_listed"],
    side_effects: "outbound HTTPS to NVD/EPSS/CISA",
    capabilities: ["network:read", "tool:nvd_fetch"],
    evidence: ["services.nvd.nist.gov", "api.first.org EPSS v1", "cisa.gov KEV catalog"],
  },

  source_trust_gate: {
    family: "decision",
    title: "Source-trust router",
    role: "Branches the run into trusted (NVD/vendor advisory), semi-trusted (third-party), or untrusted (community) ingest paths.",
    inputs: ["state.cve_record.references[] domains"],
    outputs: ["routes to canonicalize_{trusted,untrusted}; semi promotes to trusted with audit row"],
    side_effects: "none — pure routing",
    capabilities: [],
    evidence: ["allow-list lives in source_trust_audit policy table"],
  },

  // ─── Trusted lane ───────────────────────────────────────────────────────
  canonicalize_trusted: {
    family: "transform",
    title: "Canonicalize trusted text",
    role: "NFKC-normalize + markdown→AST + de-zero-width; emits canonical text alongside its watermark hash.",
    inputs: ["state.cve_record.description (multi-source merged)"],
    outputs: ["state.canonical_text", "state.canonical_watermark"],
    side_effects: "none — pure transform",
    capabilities: [],
    evidence: ["unicodedata.normalize('NFKC')", "mistletoe markdown AST"],
  },

  extract_trusted: {
    family: "llm",
    title: "Schema-constrained CVE extractor",
    role: "DSPy structured-output module mapping canonicalised CVE text into the ExtractedCve schema (cwe, vector, products[], version_ranges[]).",
    inputs: ["state.canonical_text"],
    outputs: ["state.extracted (ExtractedCve)", "state.extract_confidence"],
    side_effects: "outbound LM call",
    capabilities: ["llm:invoke"],
    evidence: ["dspy ExtractCveSignature", "Pydantic ExtractedCve schema"],
  },

  enrich_cve_trusted: {
    family: "transform",
    title: "Trusted enrichment merge",
    role: "Merges extractor output with NVD/EPSS/KEV side feeds and stamps watermark=clean on the resulting record.",
    inputs: ["state.extracted", "state.epss_score", "state.kev_listed"],
    outputs: ["state.cve_enriched", "state.watermark='clean'"],
    side_effects: "none",
    capabilities: [],
  },

  source_trust_audit: {
    family: "audit",
    title: "Source-trust audit row",
    role: "Writes one audit-trail row recording which source the trust gate accepted and why.",
    inputs: ["state.source_trust", "state.cve_record.references"],
    outputs: ["postgres.source_trust_audit row (signed Ed25519)"],
    side_effects: "audit log INSERT",
    capabilities: ["write:audit"],
    evidence: ["task #74"],
  },

  // ─── Untrusted lane (parallel) ──────────────────────────────────────────
  canonicalize_untrusted: {
    family: "transform",
    title: "Canonicalize untrusted text",
    role: "Same NFKC + AST pass as the trusted lane, but stamps quarantine_pending on the record.",
    inputs: ["state.cve_record.untrusted_text"],
    outputs: ["state.canonical_untrusted_text", "state.quarantine_pending=true"],
    side_effects: "none",
    capabilities: [],
  },

  emit_quarantine_artifact: {
    family: "artifact",
    title: "Quarantine raw text",
    role: "Persists the raw untrusted text as an ArtifactRef so audit can replay exactly what the LM saw.",
    inputs: ["state.cve_record.untrusted_text"],
    outputs: ["ArtifactRef(quarantine://CVE-…/raw.txt) → state.quarantine_artifact_ref"],
    side_effects: "blob store write",
    capabilities: ["write:artifact"],
  },

  extract_untrusted: {
    family: "llm",
    title: "Untrusted CVE extractor",
    role: "Mirror of extract_trusted but flagged untrusted_text_influenced=true on downstream retrieval edges.",
    inputs: ["state.canonical_untrusted_text"],
    outputs: ["state.extracted_untrusted", "state.untrusted_text_influenced=true"],
    side_effects: "outbound LM call",
    capabilities: ["llm:invoke"],
  },

  injection_classify: {
    family: "llm",
    title: "Prompt-injection classifier",
    role: "Classifies untrusted text as clean | suspicious | attack_pattern using the Fathom injection-detector ruleset.",
    inputs: ["state.canonical_untrusted_text"],
    outputs: ["state.injection_class ∈ {clean,suspicious,attack_pattern}"],
    side_effects: "LM call (small model)",
    capabilities: ["llm:invoke"],
    evidence: ["fathom_pack/prompt_injection_v3.clp"],
  },

  critique_extracted: {
    family: "llm",
    title: "Schema-validation critic",
    role: "Critic-shaped LM call that scores the extractor's output against the source text; <0.7 routes to HITL.",
    inputs: ["state.extracted_untrusted", "state.canonical_untrusted_text"],
    outputs: ["state.extract_critique_score (0-1)"],
    side_effects: "LM call",
    capabilities: ["llm:invoke"],
  },

  enrich_cve_untrusted: {
    family: "transform",
    title: "Untrusted enrichment merge",
    role: "Mirrors enrich_cve_trusted, but propagates untrusted_text_influenced=true through every retrieval edge.",
    inputs: ["state.extracted_untrusted", "state.epss_score", "state.kev_listed"],
    outputs: ["state.cve_enriched", "state.untrusted_text_influenced=true"],
    side_effects: "none",
    capabilities: [],
  },

  // ─── HITL gates (ingest, plan, change, retro) ───────────────────────────
  hitl_ingest_review: {
    family: "hitl",
    title: "HITL · ingest review",
    role: "Durable wait. Auto-approves when no operator is reachable inside the SLA window (offline mode); otherwise blocks for explicit approve/reject.",
    inputs: ["state.cve_enriched", "state.injection_class"],
    outputs: ["state.response.decision ∈ {approve,reject}"],
    side_effects: "POST /runs/{id}/respond expected (capability=runs:respond)",
    capabilities: ["runs:respond"],
    evidence: ["AC-14.1 interrupt action", "harbor.serve respond route"],
  },
  hitl_plan_review: {
    family: "hitl",
    title: "HITL · plan review",
    role: "Durable wait for human approval of the generated remediation plan (plan_hash, runtime, code).",
    inputs: ["state.plan_hash", "state.code_runtime", "state.code_blob"],
    outputs: ["state.response.decision ∈ {approve, replan, reject}"],
    side_effects: "blocks until POST /runs/{id}/respond",
    capabilities: ["runs:respond"],
  },
  hitl_change_approval: {
    family: "hitl",
    title: "HITL · CR approval",
    role: "Durable wait gating production rollout. Approve → progressive_execute; Reject → halt with no artifacts applied.",
    inputs: ["state.cr_number", "state.attached_artifacts[]"],
    outputs: ["state.response.decision ∈ {approve, reject}"],
    side_effects: "blocks production rollout",
    capabilities: ["runs:respond"],
  },
  hitl_retrospective_review: {
    family: "hitl",
    title: "HITL · retro review",
    role: "Optional reviewer-in-the-loop on retrospective payload before KG/Doc+ writeback.",
    inputs: ["state.retro_payload"],
    outputs: ["state.response.decision (feeds GEPA Critic signal)"],
    side_effects: "blocks Doc+ publish",
    capabilities: ["runs:respond"],
  },

  // ─── Branch passthroughs ─────────────────────────────────────────────────
  branch_resp_ingest: {
    family: "branch",
    title: "Route on ingest response",
    role: "Passthrough that routes based on hitl_ingest_review's response.decision.",
    outputs: ["approve → remediation_discovery", "reject → halt"],
  },
  branch_resp_plan: {
    family: "branch",
    title: "Route on plan response",
    role: "Passthrough routing on hitl_plan_review.response.decision.",
    outputs: ["approve → validate_dispatch", "replan → mcp_retrieval_dispatch", "reject → halt"],
  },
  branch_resp_change: {
    family: "branch",
    title: "Route on CR response",
    role: "Passthrough routing on hitl_change_approval.response.decision.",
    outputs: ["approve → progressive_execute", "reject → halt"],
  },
  branch_resp_retro: {
    family: "branch",
    title: "Route on retro response",
    role: "Passthrough routing on hitl_retrospective_review.response.decision; feeds GEPA Critic.",
    outputs: ["approve → write_retrospective", "reject → halt"],
  },

  // ─── Discovery / correlation ────────────────────────────────────────────
  remediation_discovery: {
    family: "agent",
    title: "Agentic remediation discovery",
    role: "DSPy ReAct loop. 4-source agentic lookup (advisory refs · registry latest · DDG · SearXNG) → LM JSON extraction → auto-promote upgrade/downgrade target_version.",
    inputs: ["state.cve_enriched", "state.product_refs[]"],
    outputs: ["state.fixed_version", "state.remediation_candidates[]"],
    side_effects: "outbound HTTPS + LM calls",
    capabilities: ["llm:invoke", "network:read"],
    evidence: ["[[project_cve_rem_remediation_discovery]]"],
  },

  correlate_assets: {
    family: "broker",
    title: "Asset correlation (Nautilus broker)",
    role: "Nautobot + CMDB + reachability roll-up via Nautilus broker_request. Identifies which fleet assets are vulnerable, runtime, exposure.",
    inputs: ["state.cve_enriched.products[]"],
    outputs: ["state.affected_assets[]", "state.exposed_assets[]", "state.asset_class"],
    side_effects: "outbound broker.request",
    capabilities: ["broker:invoke"],
    evidence: ["nautilus.yaml source bindings"],
  },
  suppress_not_applicable: {
    family: "decision",
    title: "Suppress not-applicable",
    role: "If correlate_assets finds zero affected assets, terminate with suppress disposition and store the negative outcome.",
    outputs: ["state.outcome='not_applicable' → halt"],
  },

  // ─── SSVC tier ─────────────────────────────────────────────────────────
  ssvc_evaluate: {
    family: "decision",
    title: "SSVC tier evaluator",
    role: "Fathom CLIPS rule-eval (cache hit) | ML pre-classifier (miss). Maps (exploitation, exposure, utility, human_impact) → tier ∈ {act_auto, act_supervised, track, defer}.",
    inputs: ["state.kev_listed", "state.exposed_assets", "state.epss_score", "state.cve_enriched.cvss"],
    outputs: ["state.ssvc_tier"],
    side_effects: "fathom CLIPS eval (in-proc)",
    capabilities: ["fathom:eval"],
    evidence: ["fathom_pack/ssvc_v2.clp"],
  },
  tier_terminal_track: {
    family: "terminal",
    title: "Tier=track terminal",
    role: "Enqueue exposure-monitor at +7d. No code generated, no CR opened.",
    outputs: ["exposure_monitor row · reevaluate_at=+7d"],
  },
  tier_terminal_defer: {
    family: "terminal",
    title: "Tier=defer terminal",
    role: "Defer with future_review_at and a freeze-window note; runs the audit/KG writeback paths but no remediation.",
    outputs: ["state.outcome='deferred'", "future_review_at"],
  },

  // ─── Plan KG / MCP retrieval ────────────────────────────────────────────
  plan_template_lookup: {
    family: "kg",
    title: "Plan-KG template lookup",
    role: "MATCH (t:PlanTemplate)-[:APPLIES_TO]->(:Cwe{id})-[:ON]->(:AssetClass{id})-[:RUNTIME]->(:Runtime{id}) — finds the best-scoring template.",
    inputs: ["state.cve_enriched.cwe", "state.asset_class", "state.runtime"],
    outputs: ["state.plan_template_id (or null)", "state.template_lookup_hit"],
    capabilities: ["graph:read"],
  },
  mcp_retrieval_dispatch: {
    family: "branch",
    title: "MCP retrieval fan-out",
    role: "Parallel fan-out to vec_search_retros, graph_prior_remediations, graph_blast_radius, framework_mapping, cargonet_lab_telemetry.",
    outputs: ["fans to 5 retrieval nodes; joined back at planner"],
  },
  vec_search_retros: {
    family: "kg",
    title: "Vec-search prior retros",
    role: "pgvector ANN over retro embeddings → top-K analogous past remediations.",
    inputs: ["embedding(state.cve_enriched.summary)"],
    outputs: ["state.retro_candidates[]"],
    capabilities: ["graph:read"],
    evidence: ["pgvector retros.embedding column"],
  },
  graph_prior_remediations: {
    family: "kg",
    title: "Plan-KG prior plans",
    role: "Cypher: prior actions taken for this product / cwe / asset_class. Anchored to overlap rule (product_overlap, cwe_overlap).",
    outputs: ["state.prior_actions[]", "state.prior_action_count"],
    capabilities: ["graph:read"],
    evidence: ["[[project_step17_phase_b_loop_closed]]"],
  },
  graph_blast_radius: {
    family: "kg",
    title: "Asset-KG blast radius",
    role: "Cypher: transitively reachable assets from each affected node, with confidence-weighted edges.",
    outputs: ["state.blast_radius_count", "state.blast_radius_assets[]"],
    capabilities: ["graph:read"],
  },
  framework_mapping: {
    family: "kg",
    title: "Doctrine-KG framework mapping",
    role: "Cypher: NIST 800-53 control + CAPEC pattern mapping for this CWE; used when sandbox is skipped to justify CR via control coverage.",
    outputs: ["state.nist_controls[]", "state.capec_patterns[]"],
    capabilities: ["graph:read"],
    evidence: ["[[project_doctrine_fallback_wired]]"],
  },
  cargonet_lab_telemetry: {
    family: "kg",
    title: "CargoNet historical traces",
    role: "Looks up historical probe traces from past sandbox runs to inform planner whether THIS class of fix has succeeded before.",
    outputs: ["state.lab_trace_count", "state.lab_success_rate"],
    capabilities: ["graph:read"],
  },

  // ─── Planner / writer / critic ──────────────────────────────────────────
  planner: {
    family: "agent",
    title: "Compiled-prompt planner",
    role: "DSPy planner ReAct loop. Compiled prompt + Reflexion buffer from prior retros + plan template hit (when present).",
    inputs: ["state.plan_template_id", "state.retro_candidates", "state.prior_actions", "state.framework_mapping"],
    outputs: ["state.plan (PlanSchema)", "state.plan_hash"],
    capabilities: ["llm:invoke"],
    evidence: ["[[project_cve_rem_planner_react_loop]]"],
  },
  code_writer: {
    family: "llm",
    title: "Multi-runtime code writer",
    role: "Emits the 4-tuple (runtime, code_blob, rollback_code, verify_probe). Runtime ∈ {ansible, k8s_yaml, terraform, container_image_bump}.",
    inputs: ["state.plan"],
    outputs: ["state.code_blob", "state.rollback_code", "state.verify_probe", "state.code_runtime"],
    capabilities: ["llm:invoke"],
  },
  emit_remediation_bundle: {
    family: "artifact",
    title: "Emit remediation bundle",
    role: "Persists code+rollback+probe+plan as one ArtifactRef so downstream consumers (sandbox, CR attach) reference a stable hash.",
    outputs: ["ArtifactRef(remediation://CVE-…/bundle.tar.gz)"],
    capabilities: ["write:artifact"],
  },
  critic: {
    family: "llm",
    title: "Fathom code-safety critic",
    role: "Critic-shaped LM call + Fathom code-safety rules → verdict ∈ {approved, feedback, veto}. Feedback loops back to planner up to 3 times.",
    inputs: ["state.code_blob", "state.plan"],
    outputs: ["state.critic_verdict", "state.critic_attempt++"],
    capabilities: ["llm:invoke", "fathom:eval"],
    evidence: ["fathom_pack/code_safety_v1.clp"],
  },

  // ─── Validation fan-out ─────────────────────────────────────────────────
  validate_dispatch: {
    family: "branch",
    title: "Validation fan-out",
    role: "Parallel fan-out to judge_safety + judge_lint; joined at validate_plan_join.",
    outputs: ["parallel → judge_safety, judge_lint"],
  },
  judge_safety: {
    family: "llm",
    title: "Code-safety judge",
    role: "Fathom code-safety re-check + watermark recheck. Independent of the planner's own critic — second pair of eyes.",
    inputs: ["state.code_blob"],
    outputs: ["state.safety_passed", "state.safety_signals[]"],
    capabilities: ["fathom:eval", "llm:invoke"],
  },
  judge_lint: {
    family: "tool",
    title: "Runtime-specific linter",
    role: "Dispatches to ansible.lint, k8s.kubeval, terraform.tflint, or container.sbom_scan based on state.code_runtime. Batfish runs for network configs.",
    inputs: ["state.code_blob", "state.code_runtime"],
    outputs: ["state.lint_passed", "state.lint_findings[]"],
    capabilities: ["tool:lint"],
  },
  validate_plan_join: {
    family: "join",
    title: "Validation convergence",
    role: "Joins judge_safety + judge_lint outputs. Sets state.validation_passed = AND of both.",
    outputs: ["state.validation_passed"],
  },

  // ─── Plan quarantine ────────────────────────────────────────────────────
  plan_quarantine_gate: {
    family: "gate",
    title: "Plan quarantine gate (F5)",
    role: "Halt-new on plan-KG quarantined plan_hash. If a previous run of THIS plan_hash diverged in production, refuses to re-apply.",
    inputs: ["state.plan_hash", "plan_kg.quarantine"],
    outputs: ["halt('plan_hash in quarantine') | passthrough"],
    capabilities: ["graph:read"],
    evidence: ["[[project_fancy_tier_b_done]] F5"],
  },

  // ─── Sandbox ────────────────────────────────────────────────────────────
  sandbox_dispatch: {
    family: "decision",
    title: "Sandbox runtime selector",
    role: "Deterministic runtime selection. Routes to sandbox_run (cargonet image present) | sandbox_skip (no lab profile → force HITL).",
    inputs: ["state.code_runtime", "cargonet.image_index"],
    outputs: ["routes to sandbox_run | sandbox_skip"],
    capabilities: [],
  },
  sandbox_run: {
    family: "sandbox",
    title: "Sandbox execution",
    role: "BEFORE/APPLY/AFTER probe. cargonet/{image} → verify the CVE reproduces, apply patch, verify it no longer reproduces, divergence_check against prod drift.",
    inputs: ["state.code_blob", "state.verify_probe"],
    outputs: ["state.sandbox_result ∈ {clean,vuln,error}", "state.sandbox_divergence"],
    capabilities: ["sandbox:run"],
  },
  sandbox_skip: {
    family: "sandbox",
    title: "Sandbox honest-skip",
    role: "No cargonet image for this runtime/asset_class. Sets state.sandbox_skipped=true and force_hitl=true.",
    outputs: ["state.sandbox_skipped=true", "state.force_hitl=true"],
    evidence: ["[[project_cve_rem_static_detection_skip]]"],
  },
  emit_sandbox_evidence: {
    family: "artifact",
    title: "Emit sandbox evidence",
    role: "Probe traces + Batfish diffs + container logs persisted as ArtifactRef for CR attachment.",
    outputs: ["ArtifactRef(sandbox://CVE-…/evidence.tar.gz)"],
    capabilities: ["write:artifact"],
  },

  // ─── Change request ─────────────────────────────────────────────────────
  create_change_request: {
    family: "tool",
    title: "Create ServiceNow CR",
    role: "POST /api/now/table/change_request via Nautilus broker. Stores CR number + sys_id in state.",
    inputs: ["state.cve_enriched", "state.affected_assets", "state.code_runtime"],
    outputs: ["state.cr_number (CHG…)", "state.cr_sys_id"],
    capabilities: ["servicenow:write"],
  },
  emit_evidence_bundle: {
    family: "artifact",
    title: "Emit evidence bundle",
    role: "plan + bundles + sandbox + JWS chain + Reflexion buffer + recon_anomaly assembled into one ArtifactRef.",
    outputs: ["ArtifactRef(evidence://CVE-…/bundle.tar.gz)"],
    capabilities: ["write:artifact"],
  },
  attach_all_artifacts: {
    family: "tool",
    title: "Attach all artifacts to CR",
    role: "Uploads every ArtifactRef (plan, sandbox, evidence, retro) + per-phase sandbox stdout to the CR via ServiceNow attachment API.",
    inputs: ["state.cr_sys_id", "every ArtifactRef in state"],
    outputs: ["sn.attachment rows", "state.attached_artifacts[]"],
    capabilities: ["servicenow:write"],
    evidence: ["task #79"],
  },

  // ─── Progressive execute ───────────────────────────────────────────────
  progressive_execute: {
    family: "tool",
    title: "Progressive execute (canary→stage→fleet)",
    role: "Drives the runtime adapter (Ansible/k8s/terraform) through 3 waves with verify_probe between each.",
    inputs: ["state.code_blob", "state.code_runtime", "state.affected_assets"],
    outputs: ["state.applied_assets[]", "state.execute_result"],
    capabilities: ["runtime:apply"],
  },
  partial_apply_rollback: {
    family: "tool",
    title: "Partial-apply rollback",
    role: "Fail branch from progressive_execute. Applies rollback_code to assets that completed apply; preserves per-asset ledger.",
    capabilities: ["runtime:apply"],
  },
  verify_immediate: {
    family: "tool",
    title: "Immediate verification",
    role: "Runs the verify_probe against every applied asset. Failures route to divergence_quarantine.",
    inputs: ["state.applied_assets", "state.verify_probe"],
    outputs: ["state.verify_passed (per-asset)"],
    capabilities: ["runtime:probe"],
  },
  divergence_quarantine: {
    family: "gate",
    title: "Divergence quarantine",
    role: "Sandbox-prod disagreement → quarantine the plan_hash (F5) and emit GEPA record.",
    capabilities: ["graph:write"],
    evidence: ["[[project_fancy_tier_b_done]] F5"],
  },
  drift_watch_spawn: {
    family: "tool",
    title: "Spawn drift-watch run",
    role: "POST /v1/runs to the triggered drift_watch graph so post-deploy drift gets monitored automatically.",
    outputs: ["child_run_id"],
    capabilities: ["runs:create"],
  },

  // ─── Retro / KG writeback ──────────────────────────────────────────────
  write_retrospective: {
    family: "transform",
    title: "Assemble retro payload",
    role: "Detector emits structured signals from observable state; LM synthesises citation-bound prevention suggestions.",
    outputs: ["state.retro_payload"],
    evidence: ["[[project_cve_rem_retro_failure_analysis]]"],
  },
  kg_run_writeback: {
    family: "kg",
    title: "Runtime-KG UPSERT",
    role: "UPSERT (CVE, Action, Run, CI, Product) nodes + (PATCHED, ON_RUN, AFFECTS) edges into the runtime KG.",
    outputs: ["plan_kg edges with outcome=patched|rollback"],
    capabilities: ["graph:write"],
  },
  emit_retro_payload: {
    family: "artifact",
    title: "Emit retro ArtifactRef",
    role: "Persists retro payload as ArtifactRef for cargonet/plan-KG writebacks and Doc+ doc generation.",
    capabilities: ["write:artifact"],
  },
  render_docx: {
    family: "tool",
    title: "Render Doc+ DOCX",
    role: "python-docx + Jinja2 narrative render. Tables for affected assets, CR ref, sandbox results, retro suggestions.",
    outputs: ["state.docx_blob"],
  },
  emit_docx_archive: {
    family: "artifact",
    title: "Emit DOCX archive",
    role: "Persists rendered DOCX as ArtifactRef (also serves as Doc+ staging).",
    capabilities: ["write:artifact"],
  },
  retro_dispatch: {
    family: "branch",
    title: "Retro fan-out",
    role: "Parallel fan-out to publish_docplus + cargonet_writeback + plan_kg_writeback.",
  },
  publish_docplus: {
    family: "tool",
    title: "Doc+ publish",
    role: "Creates collection + per-CVE doc + m2m link; uploads DOCX attachment. Idempotent per CVE.",
    capabilities: ["servicenow:write"],
    evidence: ["[[project_cve_rem_docplus_tables]]"],
  },
  cargonet_writeback: {
    family: "tool",
    title: "CargoNet writeback",
    role: "Visibility-only writeback. Stores sandbox trace + outcome for future similar runs.",
    capabilities: ["graph:write"],
  },
  plan_kg_writeback: {
    family: "kg",
    title: "Plan-KG VERIFIED_ON",
    role: "Adds VERIFIED_ON edge with outcome=patched|rollback so future planner runs can score this plan_hash by past success.",
    capabilities: ["graph:write"],
  },
  retro_join: {
    family: "join",
    title: "Retro join",
    role: "Joins the 3-way retro fan-out before krakntrust_attest.",
  },
  krakntrust_attest: {
    family: "tool",
    title: "KraknTrust attestation",
    role: "Ed25519-signs the run attestation (run_id, outputs hash, capability set) and attaches to the CR.",
    outputs: ["state.attestation_jws"],
    capabilities: ["sign:attest"],
    evidence: ["[[project_fancy_tier_a_done]] F1"],
  },
  run_outcome_persist: {
    family: "tool",
    title: "Persist run outcome (F12)",
    role: "Writes outcome row to fleet_outcomes table. Feeds halt_new_gate on subsequent runs.",
    capabilities: ["write:fleet_outcomes"],
    evidence: ["[[project_fancy_tier_b_hardened]] F12-1"],
  },
  cr_self_validate: {
    family: "tool",
    title: "CR self-validate (refetch)",
    role: "Refetches the CR from ServiceNow and asserts substance thresholds (≥N attachments, work_notes non-empty, doc+ link present).",
    capabilities: ["servicenow:read"],
    evidence: ["task #84"],
  },
  emit_proof_report: {
    family: "artifact",
    title: "Emit proof report (Markdown)",
    role: "Single Markdown auditor report attached to CR; one-page summary of inputs/decisions/outputs/sign.",
    capabilities: ["write:artifact", "servicenow:write"],
    evidence: ["task #86"],
  },

  // ─── Terminal sentinel ──────────────────────────────────────────────────
  action_done: {
    family: "terminal",
    title: "Run terminal",
    role: "End-of-graph sentinel. No side effects.",
  },
};

const FAMILY_BADGE = {
  gate:      { label: "GATE",        color: "#f5b54a" },
  source:    { label: "SOURCE",      color: "#5cdfe6" },
  decision:  { label: "DECISION",    color: "#f5b54a" },
  transform: { label: "TRANSFORM",   color: "#a89cff" },
  llm:       { label: "LLM",         color: "#3ddc97" },
  audit:     { label: "AUDIT",       color: "#a89cff" },
  artifact:  { label: "ARTIFACT",    color: "#5cdfe6" },
  hitl:      { label: "HITL",        color: "#f5b54a" },
  branch:    { label: "BRANCH",      color: "#a89cff" },
  agent:     { label: "AGENT",       color: "#3ddc97" },
  broker:    { label: "BROKER",      color: "#5cdfe6" },
  kg:        { label: "KG",          color: "#5cdfe6" },
  tool:      { label: "TOOL",        color: "#3ddc97" },
  sandbox:   { label: "SANDBOX",     color: "#a89cff" },
  join:      { label: "JOIN",        color: "#a89cff" },
  terminal:  { label: "TERMINAL",    color: "#888" },
};

Object.assign(window, { NODE_PROFILE, FAMILY_BADGE });
