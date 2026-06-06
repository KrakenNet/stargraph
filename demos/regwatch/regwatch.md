=============================================================================
                       REGULATORY CHANGE WATCHER
=============================================================================

  Pitch:  Daily agent reads FDA / SEC / CFPB / NIST / EU regulatory feeds,
          diffs against yesterday's regulatory state, finds the deltas,
          links each delta to internal policy docs that reference the
          affected rule, and emails a triage list to compliance officers.
          On-prem, signed-policy, air-gap-friendly.

  Audience:  Compliance teams in regulated industries -- they have
             budget, they have pain, they currently use grep + humans.

=============================================================================

[stargraph.triggers.cron  -- 04:00 daily]
             │
             ▼
[FetchRegFeedsTool]
             │  Pulls from a configured list of feeds (RSS / .gov pages /
             │  EUR-Lex / Federal Register XML).
             │
             ▼
[NormalizeNode]
             │  Per source, extracts (rule_id, section, body, effective_date).
             │  Each fact stamped with full ProvenanceBundle:
             │    origin=fda.gov/..., captured_at=ISO, author='FDA',
             │    contributor=Stargraph pipeline.
             │
             ▼
[DiffNode]
             │  Compares to FactStore snapshot from yesterday.
             │  Emits typed deltas:
             │    NewRule | AmendedRule | WithdrawnRule | EffectiveDateChange
             │
             ▼
[CrossReferenceNode]
             │  For each delta, queries:
             │    - GraphStore (kuzu): internal_policy --references--> rule_id
             │    - VectorStore (lancedb): semantic similarity over policy text
             │    - DocStore: full policy text for context
             │  Emits affected_policy_set per delta.
             │
             ▼
[RetrievalNode (RRF fusion)]
             │  Top-K prior amendments to similar rules + how the team
             │  responded last time (institutional memory).
             │
             ▼
[DSPyAdapter: write_briefing]
             │  Drafts a per-delta brief:
             │    - what changed (with citations)
             │    - which internal docs cite it
             │    - urgency (effective_date proximity)
             │    - suggested owner (from policy doc metadata)
             │
             ▼
[Fathom governance gate -- 'compliance-policy' Bosun pack]
             │  CLIPS rules:
             │    - effective_date <= today + 30d   -> mark URGENT
             │    - rule.agency == 'FDA' && policy
             │      touches PHI                     -> route to legal-pharma
             │    - delta.kind == WithdrawnRule
             │      and any policy still cites it   -> block ship of brief
             │                                         (must reconcile first)
             │
             ▼
[WriteArtifactNode]
             │  Daily brief as BLAKE3 content-addressable PDF + JSON.
             │
             ▼
[EmailSendTool]
             │  Sends to compliance@... with brief artifact attached.
             │  Saves message-id for audit chain.
             │
             ▼
[stargraph.audit.JSONLAuditSink]
             Ed25519-signed: feeds_fetched, deltas, citations, brief_hash,
             email_message_id, ruleset_hash.

   ┌────────── On-prem / air-gap mode ──────────┐
   │ Disable external fetch tools, ingest feeds │
   │ via signed bundle drop. MiniLM weights     │
   │ pinned (sha256). All policy lookups local. │
   │ Same pipeline; no network egress.          │
   └────────────────────────────────────────────┘

=============================================================================
                              WHY IT LANDS
=============================================================================

- Real, recurring, expensive problem with a budget line.
- Compliance buyers are allergic to "AI suggestions" without provenance.
  This pipeline IS provenance.
- The on-prem / air-gap variant lights up Stargraph's signed-policy +
  pinned-weights story for buyers that can't move data off-prem.
- 5-minute investment for the user (point at 5 feeds, list 50 policies);
  hours of compliance work saved per day.

=============================================================================
                         STARGRAPH CAPABILITIES EXERCISED
=============================================================================

  Triggers:      cron (daily)
  Stores:        FactStore (snapshot), GraphStore (policy <-> rule),
                 VectorStore (semantic), DocStore (policy text)
  Skills:        autoresearch (web fetch path), rag (cross-ref path)
  Nodes:         RetrievalNode, DSPyAdapter, custom Diff/CrossReference
  Embeddings:    MiniLM, sha256-pinned
  Tools:         FetchRegFeedsTool, EmailSendTool (@tool decorated)
  Provenance:    Mandatory on every fact (origin, captured_at, agency)
  Fathom:        Compliance CLIPS rules
  Bosun:         Signed 'compliance-policy' pack
  Artifacts:     Daily brief as content-addressable PDF
  Audit:         Ed25519 JSONL daily chain
  Air-gap:       Variant config in fixtures/airgap/

=============================================================================
                              DEMO FOOTPRINT
=============================================================================

  demos/regwatch/
    README.md
    stargraph.yaml
    bosun-packs/
      compliance-policy/          -- signed
    config/
      feeds.yaml                  -- 8 sample regulatory feeds
      policies.yaml               -- 30 sample internal policy docs
    data/
      yesterday_snapshot.jsonl    -- baseline FactStore content
      sample_amendments/          -- 5 realistic regulatory deltas
    fixtures/
      airgap/                     -- offline mode config
    scripts/
      bootstrap.sh
      run_daily.sh
      airgap_bundle.sh            -- builds reproducible offline bundle
    Makefile
      make demo                   -- end-to-end with sample feeds
      make airgap                 -- offline reproducible run
      make brief                  -- emit today's brief without sending
      make audit                  -- inspect signed audit chain
