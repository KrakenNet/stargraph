=============================================================================
              CUSTOMER SUPPORT TRIAGE WITH GOVERNANCE VETO
=============================================================================

  Pitch:  Tier-1 support bot for a SaaS. Answers from KB, escalates when
          uncertain, and a Fathom CLIPS rule fires `interrupt` mid-run if
          (a) PII appears, (b) a refund request crosses a $ threshold, or
          (c) the customer mentions a regulator. Replay any conversation
          for QA scoring or dispute review.

  Audience:  Every CX vendor has a chatbot. None can prove what their bot
             did, replay it, or hot-swap escalation policy. Stargraph can.

=============================================================================

[stargraph.serve  -- /v1/support/chat (POST, mTLS or session JWT)]
             │
             ├──► [Auth + capability gate (support.handle_inbound)]
             ├──► [Rate limit per session]
             │
             ▼
[SessionContextNode]
             │
             ├──► MemoryStore   -- prior turns this session
             ├──► FactStore     -- account: plan, MRR, region, status
             ├──► DocStore      -- KB articles
             └──► GraphStore    -- entity graph (account, products, tickets)
             │
             ▼
[ClassifyIntentNode  (DSPy)]
             │  Output: intent in {howto, billing, refund, bug, other}
             │
             ▼
[Mirror -> Fathom facts]
             │  State -> CLIPS:  intent, mention_pii, mention_regulator,
             │                   refund_amount, sentiment
             │
             ▼
[Fathom adapter evaluates -- 'support-policy' signed Bosun pack]
             │  CLIPS rules:
             │    (mention_pii=true)              -> InterruptAction(reason=pii)
             │    (intent=refund && amount>200)   -> InterruptAction(reason=refund_thresh)
             │    (mention_regulator=true)        -> InterruptAction(reason=regulator)
             │    (sentiment=very_negative)       -> RouteToHumanAction
             │  Each firing emits fact w/ ProvenanceBundle.
             │
             ├──► If any InterruptAction fires:
             │       persist checkpoint, surface case to human queue,
             │       return holding response to user, end turn.
             │
             ▼ (otherwise)
[RetrievalNode (RRF fusion)]
             │  Top-K KB chunks for this query within this account context.
             │
             ▼
[ReplyDraftNode (DSPy)]
             │  Drafts answer with inline KB citations.
             │
             ▼
[Fathom 'safety_pii' pack -- final scrub]
             │  Strips/blocks any PII in outbound reply.
             │
             ▼
[WriteArtifactNode]
             │  Conversation turn (input + retrieved + reply + citations)
             │  as BLAKE3 content-addressable artifact.
             │
             ▼
[stargraph.audit.JSONLAuditSink]
             Ed25519-signed: session_id, intent, retrieved_chunks,
             rule_firings, action, reply_hash.

   ┌─── Hot-swap policy without redeploy ───┐
   │  ops$ stargraph bosun load support-policy │
   │       --pack ./packs/support-v3.bsn    │
   │  -> sig verified, ruleset hash logged, │
   │     next request uses new rules.       │
   └────────────────────────────────────────┘

   ┌─── 6 weeks later: customer disputes a refund ───┐
   │  stargraph replay --session 14881                  │
   │  -> byte-identical reproduction of the chat,    │
   │     incl. retrieved KB snapshot + ruleset hash. │
   └─────────────────────────────────────────────────┘

=============================================================================
                              WHY IT LANDS
=============================================================================

- Boring use case (every team has a support bot), killer differentiator
  (audit + replay + signed policy hot-swap).
- The interrupt action shows real human-in-the-loop -- not a vague
  "and then a human reviews it" handwave.
- Bosun pack hot-swap is a story sales teams love: "you don't redeploy
  to change escalation policy."
- Hooks naturally into existing CRMs / ticket queues via custom @tools.

=============================================================================
                         STARGRAPH CAPABILITIES EXERCISED
=============================================================================

  Stores:        Memory, Fact, Doc, Graph (4 of 5 protocols)
  Skills:        rag (KB lookup)
  Nodes:         Retrieval (RRF), DSPy x2, custom Session/Reply, Artifact
  Mirror:        State -> CLIPS facts each step
  Fathom:        Forward-chaining rules; interrupt + route + reply actions
  Bosun:         Two signed packs (support-policy, safety_pii)
  Hot-swap:      Live ruleset reload via Fathom v0.3.1 hot-reload
  Auth:          mTLS or session JWT; capability gates
  HTTP:          /v1/support/chat endpoint via stargraph.serve
  Replay:        Cassettes per session for QA / dispute
  Audit:         Ed25519 JSONL chain
  Artifacts:     Per-turn content-addressable storage

=============================================================================
                              DEMO FOOTPRINT
=============================================================================

  demos/support-veto/
    README.md
    stargraph.yaml
    bosun-packs/
      support-policy-v1/          -- signed
      support-policy-v2/          -- signed; hot-swap target
      safety-pii/                 -- signed
    data/
      kb/                         -- 80 KB articles
      accounts.csv                -- 100 sample accounts
    fixtures/
      conversations/
        001_normal_howto.jsonl
        002_refund_under_threshold.jsonl
        003_refund_over_threshold.jsonl   -- triggers veto
        004_pii_leak.jsonl                -- triggers veto
        005_regulator_mention.jsonl       -- triggers veto
    scripts/
      bootstrap.sh
      simulate_conversation.sh    -- play a fixture against the agent
      hot_swap_demo.sh            -- v1 -> v2 mid-session
      dispute_replay.sh           -- 6-weeks-later story
    Makefile
      make demo                   -- runs all 5 fixtures
      make hot-swap               -- the showcase
      make replay SESSION=14881
      make audit
