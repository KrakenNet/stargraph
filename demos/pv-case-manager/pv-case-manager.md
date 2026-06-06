=============================================================================
                  PHARMACOVIGILANCE CASE MANAGER
                      ( the master-of-all demo )
=============================================================================

  Pitch:  Adverse-drug-event triage for a pharma company. Cases stream in
          24/7 from FAERS / MAUDE / sponsor sites / call centers. Each
          case must be triaged for seriousness, expedited reportability
          (FDA 7-day / 15-day windows), and quality completeness, with
          a fully reconstructible decision trail that survives FDA
          inspection 3 years later.

          Pharmacovigilance is a multi-billion-dollar industry that has
          refused to adopt AI agents specifically because nothing on the
          market is auditable enough. Stargraph is.

  Footprint:  Touches ~19 distinct Stargraph capabilities -- effectively
              the whole src/stargraph/* tree -- in one realistic workflow.
              Swap "pharmacovigilance" for "clinical trial pharmacy",
              "medical device complaint", "CFPB adverse-action",
              "defense incident triage", or "trade-surveillance review"
              and the architecture is identical.

=============================================================================
                              FLOW DIAGRAM
=============================================================================

  ┌─── Inbound ───────────────────────────────────────────────────────┐
  │ stargraph.triggers.webhook  -- new AE from FAERS/sponsor portal       │
  │ stargraph.triggers.cron     -- 02:00 nightly batch reconciliation     │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── stargraph.serve  -- /v1/runs ─────────────────────────────────────┐
  │ Auth:  mTLS (sponsor portal) or API key (internal)                 │
  │ Caps:  pv.case.write                                               │
  │ Rate:  per-sponsor + per-asset limits                              │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── Hydrate context (parallel) ────────────────────────────────────┐
  │ MemoryNode    -- prior cases for this patient/drug pair            │
  │ FactStore     -- lab values, vital signs                           │
  │ GraphStore    -- drug-drug interaction map (kuzu)                  │
  │ DocStore      -- sponsor case files (sqlite_doc)                   │
  │ VectorStore   -- pinned PubMed corpus (lancedb + MiniLM sha256)    │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── RetrievalNode ─────────────────────────────────────────────────┐
  │ RRF fusion across vector + graph + doc + fact stores               │
  │ Cypher subset linter prevents prompt-injected graph queries        │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── MLNode ────────────────────────────────────────────────────────┐
  │ Seriousness classifier (ONNX, sha256-pinned)                       │
  │ Loaded via stargraph.ml.ModelRegistry                                 │
  │ Output: probability(serious), causality, expectedness, narrative   │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── DSPyAdapter (case-type router) ────────────────────────────────┐
  │ stargraph.adapters.dspy.bind() with LoudFallbackFilter                │
  │ Routes to specialized SubGraphNode by case type:                   │
  │   - oncology     -> OncologySubGraph                               │
  │   - cardiac      -> CardiacSubGraph                                │
  │   - pediatric    -> PediatricSubGraph                              │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── External enrichment (gated) ───────────────────────────────────┐
  │ Nautilus broker_node -- drug master data, MedDRA terms             │
  │ Bosun 'budgets' pack rate-limits external calls per case           │
  │ stargraph.adapters.mcp -- exposes our tools to upstream EHR systems   │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── Mirror state -> Fathom facts ──────────────────────────────────┐
  │ stargraph.runtime.mirror_lifecycle bucket = step                      │
  │ Mirror-annotated state fields auto-emit AssertSpec on each step    │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── Fathom CLIPS rules from FOUR signed Bosun packs ───────────────┐
  │                                                                    │
  │ pack: reportability                                                │
  │   (seriousness=fatal && within_window=false) -> ExpediteAction     │
  │   (causality=probable && unexpected=true)    -> RequireMDReview    │
  │   (drug=on_protocol && deviation=true)       -> FlagProtocolDev    │
  │                                                                    │
  │ pack: audit                                                        │
  │   every state-mutation -> assert audit_event w/ ProvenanceBundle   │
  │                                                                    │
  │ pack: budgets                                                      │
  │   tokens or $ exceed cap -> AbortWithReason                        │
  │   external call retry policy (regulator-approved)                  │
  │                                                                    │
  │ pack: safety_pii                                                   │
  │   HIPAA redaction at every state transition                        │
  │   PHI never leaves the deterministic boundary                      │
  │                                                                    │
  │ All packs: Ed25519 + EdDSA-JWT signed, FilesystemTrustStore TOFU   │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── ActionDispatch ────────────────────────────────────────────────┐
  │ stargraph_action facts -> typed Action instances                      │
  │   ExpediteAction | RequireMDReview | InterruptAction | FlagAction  │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── InterruptNode (HITL) ──────────────────────────────────────────┐
  │ Pauses run; persists checkpoint                                    │
  │ Pharmacist signs off via /v1/runs/{id}/respond                     │
  │ Resume continues exactly where it stopped                          │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── WriteArtifactNode ─────────────────────────────────────────────┐
  │ Signed PDF case report                                             │
  │ FilesystemArtifactStore, BLAKE3 content-addressable                │
  │ artifact_id is the SHA -- can't be tampered with                   │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── Persistence ───────────────────────────────────────────────────┐
  │ Checkpointer after every node                                      │
  │   sqlite (dev) / postgres (prod) -- migrations _m001/_m002         │
  │ stargraph.audit.JSONLAuditSink: Ed25519-signed chain                  │
  │   alert_id, retrieved_context, model_hash, ruleset_hash,           │
  │   rule_firings, action, operator (if HITL), artifact_hash          │
  └─────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
  ┌─── Memory consolidation (cron 03:00) ─────────────────────────────┐
  │ stores.kg_promotion: frequent (patient, drug, outcome) triples     │
  │   -> promote MemoryStore rows to GraphStore relationships          │
  │ mirror_lifecycle.flush(pinned)                                     │
  └───────────────────────────────────────────────────────────────────┘

  ┌────────────── 3 years later: FDA inspection ──────────────────────┐
  │ Inspector: "Why didn't you expedite case 14592?"                   │
  │                                                                    │
  │ Operator runs:                                                     │
  │   stargraph replay --run-id 14592                                     │
  │     -> byte-identical reproduction of IR, ruleset hash, model      │
  │        hash, lit-corpus snapshot                                   │
  │                                                                    │
  │   stargraph counterfactual --run-id 14592 \                           │
  │       --mutate "lab.creatinine=2.1"                                │
  │     -> shows what the decision would have been with that lab       │
  │                                                                    │
  │   stargraph replay --run-id 14592 --pack-version reportability@v3     │
  │     -> shows what would have happened under the older policy       │
  └───────────────────────────────────────────────────────────────────┘

  ┌────────────── Air-gap deployment (hospital network) ──────────────┐
  │ - MiniLM safetensors pinned by sha256 (AC-11.2)                    │
  │ - Bosun packs signed (Ed25519 + EdDSA-JWT)                         │
  │ - FilesystemTrustStore TOFU                                        │
  │ - Cypher subset linter blocks arbitrary graph queries              │
  │ - Whole bundle reproducible on a disconnected machine              │
  │ make airgap-bundle  -- emits stargraph-pv-<sha>.tar.gz                │
  └───────────────────────────────────────────────────────────────────┘

=============================================================================
                              WHY IT LANDS
=============================================================================

- Real, regulated workflow with measurable cost-of-failure.
- Every Stargraph distinctive feature is load-bearing, not decorative.
- The architecture re-skins to any regulated industry by swapping
  vocabulary -- one demo, five vertical sales pitches.
- "Three years later, replay this decision" is the line that closes
  enterprise deals.
- Air-gap variant lights up hospital / classified / on-prem-only
  buyer profile that competitors can't serve.

=============================================================================
                  STARGRAPH CAPABILITIES EXERCISED  (count: 19)
=============================================================================

  1.  Pluggable stores (5 protocols)         RetrievalNode RRF fusion
  2.  Reference skills (rag/autoresearch)    Lit search subgraph
  3.  ML model registry + sha256 pinning     Seriousness classifier
  4.  DSPy + MCP adapters                    Case routing + EHR bridge
  5.  Fathom governance + stargraph_action      Reportability rules
  6.  Bosun signed packs (4 packs)           audit/budgets/retries/safety_pii
  7.  Mandatory provenance bundle            Every fact, FDA-grade
  8.  BLAKE3 content-addressable artifacts   Case report PDF
  9.  Ed25519 JSONL audit                    Inspector-ready chain
  10. Triggers (cron + webhook)              Nightly + real-time
  11. mTLS + API key + capabilities          Sponsor portal auth
  12. Checkpoint (SQLite + Postgres)         Per-step durability
  13. Cassettes + counterfactual replay      "Why didn't you expedite?"
  14. Air-gap + pinned weights               On-prem hospital deployment
  15. Tools framework + Nautilus broker      External enrichment
  16. Memory -> KG promotion                 Nightly consolidation
  17. Mirror lifecycle (run/step/pinned)     State -> fact mirroring
  18. HTTP serve + OpenAPI 3.1               Sponsor REST API
  19. Lineage audit script                   CI gate + FDA evidence

=============================================================================
                              DEMO FOOTPRINT
=============================================================================

  demos/pv-case-manager/
    README.md                         -- pitch + run instructions
    stargraph.yaml                       -- graph definition, stores, triggers
    bosun-packs/
      audit/                          -- signed
      budgets/                        -- signed
      safety-pii/                     -- signed
      reportability/                  -- signed
    data/
      faers_sample.jsonl              -- 10 synthetic AE cases
      literature_corpus/              -- pinned PubMed abstracts
      meddra_terms.csv                -- coding dictionary
      drug_interactions.csv           -- DDI graph seed
    models/
      seriousness_classifier.onnx     -- sha256-pinned
      embedding_minilm.safetensors    -- sha256-pinned
    fixtures/
      cassette_case_14592/            -- full deterministic replay bundle
      cassette_case_14592_alt/        -- counterfactual: with lab value
    scripts/
      bootstrap.sh                    -- uv install + sign packs + load corpus
      inspect_audit.py                -- pretty-print signed chain
      fda_replay_drill.sh             -- the auditor demo
      mcp_bridge_demo.sh              -- expose tools to mock EHR
    Makefile
      make demo                       -- end-to-end one-cmd run
      make replay                     -- reproduce case 14592
      make counterfactual             -- mutate one fact, diff the outcome
      make airgap-bundle              -- tar.gz reproducible bundle
      make audit-fda                  -- emit inspector packet
      make hot-swap                   -- swap reportability pack v2 -> v3
