=============================================================================
                       SOC L1 ALERT TRIAGE
              (generalization of demos/atr/ for SOC operations)
=============================================================================

  Pitch:  Security operations agent reads SIEM alerts as they arrive,
          gathers context (user, asset, recent activity, IOCs, runbook),
          classifies disposition (false-positive / escalate / auto-remediate),
          drafts the case note, and emits a signed audit chain that holds
          up under SOC2 / ISO27001 / customer audit.

  Audience:  Enterprise security teams that want to deploy an agent but
             can't get past the audit committee.

=============================================================================

[SIEM webhook -- new alert]
             │
             ▼
[stargraph.triggers.webhook]
             │  Validates payload schema; emits TriggerEvent.
             │
             ▼
[stargraph.security.capabilities  -- gate alerts.read]
             │
             ▼
[ContextHydrationNode]
             │
             ├──► FactStore (sqlite_fact)
             │     -- IOC database, recent firings, kill-chain stage
             ├──► GraphStore (kuzu)
             │     -- asset relationships, owner graph, blast radius
             ├──► DocStore  (sqlite_doc)
             │     -- runbooks for this alert family
             ├──► MemoryStore
             │     -- prior triage outcomes for this user/asset
             └──► Nautilus broker_node
                   -- CVE enrichment, threat-intel lookup
                      (rate-limited via Bosun budgets pack)
             │
             ▼
[RetrievalNode (RRF fusion)]
             │  Pulls top-K closest historical incidents.
             ▼
[MLNode: severity scorer]
             │  ONNX model from ModelRegistry (sha256-pinned).
             │  Inputs: alert features + retrieved priors.
             │  Output: probability(true_positive), confidence band.
             ▼
[DSPyAdapter: triage_decide]
             │  Drafts: disposition + reasoning + recommended action.
             ▼
[Fathom governance gate -- signed Bosun pack 'soc-policy']
             │  CLIPS rules:
             │    - asset.tier == prod && action == auto-remediate
             │        -> InterruptAction (require human sign-off)
             │    - asset.owner == exec && severity >= high
             │        -> RouteToTier3 (escalate)
             │    - confidence < 0.6
             │        -> RequireSecondOpinion (run twice with different model)
             │    - any rule firing emits fact with full ProvenanceBundle
             │
             ▼
[ActionDispatch]
             │  Translates stargraph_action facts into typed Action instances:
             │    AutoRemediateAction | InterruptAction | EscalateAction
             ▼
[WriteArtifactNode]
             │  Case note as BLAKE3 content-addressable PDF.
             ▼
[stargraph.audit.JSONLAuditSink]
             │  Every step Ed25519-signed:
             │    alert_id, retrieved_context, model_hash, ruleset_hash,
             │    rule_firings, action, operator (if HITL).
             │
             ▼
[Slack/PagerDuty/ServiceNow tools]
             Notify based on action type.

       ┌────── 90 days later ──────┐
       │ Auditor: "Why did you     │
       │ auto-close case 8821?"    │
       └────────────┬──────────────┘
                    │
                    ▼
            stargraph replay --run-id 8821
            (byte-identical reproduction)
                    │
                    ▼
            stargraph counterfactual --mutate 'asset.tier=prod'
            (shows what would have happened)

=============================================================================
                              WHY IT LANDS
=============================================================================

- Direct analog to atr/, instantly recognizable to enterprise security.
- Exercises every distinctive Stargraph feature without being contrived.
- Auditor story is the entire pitch: nobody else can replay decisions
  cryptographically months later.
- Bosun signed packs let SOC managers update policy without redeploying
  the agent -- a real operational requirement.

=============================================================================
                         STARGRAPH CAPABILITIES EXERCISED
=============================================================================

  Triggers:      webhook (SIEM)
  Stores:        FactStore, GraphStore, DocStore, MemoryStore (4 of 5)
  Embeddings:    MiniLM
  Nodes:         Retrieval (RRF), ML, DSPy, Custom (ContextHydration), Artifact
  Tools:         Nautilus broker_request, Slack/PD/ServiceNow @tool
  ML registry:   sha256-pinned severity classifier
  Fathom:        CLIPS rules, stargraph_action mirror, InterruptAction
  Bosun:         Signed 'soc-policy' pack + 'budgets' pack
  Capabilities:  Capability gate before any state read
  Replay:        Cassettes + counterfactual
  Audit:         Ed25519 JSONL chain
  Artifacts:     BLAKE3 case note PDFs

=============================================================================
                              DEMO FOOTPRINT
=============================================================================

  demos/soc-triage/
    README.md
    stargraph.yaml
    bosun-packs/
      soc-policy/                 -- signed; the 4 rules above
      budgets/                    -- per-alert token / $ caps
    data/
      alerts_sample.jsonl         -- 50 synthetic SIEM alerts
      assets.csv                  -- 200 assets with tier/owner
      runbooks/                   -- 12 runbooks for alert families
      priors/                     -- 1000 historical triage outcomes
    models/
      severity_classifier.onnx    -- sha256-pinned
    fixtures/
      cassette_case_8821/         -- demoable counterfactual
    scripts/
      bootstrap.sh
      replay_drill.sh             -- the auditor demo
      compare_with_atr.sh         -- shows continuity with atr/
    Makefile
      make demo
      make replay CASE=8821
      make counterfactual CASE=8821 MUTATE='asset.tier=prod'
      make audit-packet           -- emits inspector-ready bundle
