=============================================================================
                          PR REVIEW ASSISTANT
                  (companion demo to code-graph)
=============================================================================

  Pitch:  GitHub Action that reads a PR diff, queries the code-graph
          knowledge graph of the repo, and posts a review comment grounded
          in actual call paths and architectural impact -- not surface
          regex patterns. When a reviewer disagrees, replay the exact
          review with the cassette to debug WHY the agent said what it said.

  Pairs with:  demos/code-graph/  (one builds the map, this one drives on it)

=============================================================================

[GitHub webhook -- PR opened/synchronize]
             │
             ▼
[stargraph.triggers.webhook receives event]
             │
             ▼
[FetchDiffTool]
             │  Pulls unified diff + changed file list
             ▼
[BlastRadiusNode]
             │  Queries code-graph (KuzuGraphStore)
             │  for every changed symbol:
             │    - direct callers
             │    - downstream call paths (depth=3)
             │    - tests covering symbol
             │  Cypher subset (whitelist linter) prevents prompt-injection.
             │
             ▼
[RetrievalNode (RRF fusion)]
             │  - VectorStore: similar past PRs/commits
             │  - DocStore:    relevant ADRs and design docs
             │  - GraphStore:  affected modules + their owners
             │
             ▼
[DSPyAdapter: review_pr]
             │  Inputs:  diff + blast radius + retrieved priors
             │  Output:  structured review (overall + per-file + risk score)
             │
             ▼
[Fathom governance check]
             │  CLIPS rules from signed Bosun pack:
             │    - touches src/stargraph/ir/_dumps.py     -> require @ir-lead
             │    - changes >500 LOC                    -> request 2 reviewers
             │    - removes a test without adding one   -> block
             │    - api_version major bumped            -> require ADR link
             │  Each rule firing produces a fact w/ provenance.
             │
             ▼
[PostCommentTool]  (gh api wrapper)
             │  Posts review as a single PR comment.
             │
             ▼
[stargraph.audit.JSONLAuditSink]
             │  Ed25519-signed record: PR id, ruleset hash, retrieved
             │  symbols, model id, prompt hash, output hash.
             │
             └──► Months later, "why did the bot block my PR?" ──►
                  stargraph replay --run-id <id>     # byte-identical
                  stargraph counterfactual ...       # what if rule X off?

=============================================================================
                              WHY IT LANDS
=============================================================================

- Pairs naturally with code-graph (one builds, one drives) -- two demos,
  one continuous narrative.
- Concrete, demoable in a 90-second video on a public-fork PR.
- Differentiator: most PR bots regex over the diff. This one knows the
  repo's actual call graph.
- Replay story is unique: nobody else can say "let me show you exactly
  why the bot decided this 3 months later."

=============================================================================
                         STARGRAPH CAPABILITIES EXERCISED
=============================================================================

  Triggers:      webhook (GitHub PR events)
  Stores:        GraphStore (kuzu), VectorStore (lancedb), DocStore (sqlite)
  Skills:        rag, react (subgraph)
  Nodes:         RetrievalNode, DSPyAdapter, custom BlastRadiusNode
  Tools:         @tool decorated (FetchDiffTool, PostCommentTool)
  Governance:    Fathom CLIPS pack with PR-policy rules
  Bosun:         Signed pack -- swap policies without redeploy
  Cypher subset: Whitelist linter (defends against prompt-injected queries)
  Replay:        Cassettes per run; counterfactual against rule pack
  Audit:         JSONLAuditSink with model+prompt hashes

=============================================================================
                              DEMO FOOTPRINT
=============================================================================

  demos/pr-review/
    README.md
    stargraph.yaml
    bosun-packs/
      pr-policy/                  -- signed; rules above
    fixtures/
      sample_repo_graph.json      -- pre-built graphify output
      sample_prs/
        001_simple_typo.diff
        002_api_bump.diff
        003_test_removal.diff
        004_ir_dumps_change.diff
    scripts/
      bootstrap.sh                -- sign packs, load fixtures
      review_local.sh             -- run against a local diff
      gh_action.yml               -- drop-in workflow file
    Makefile
      make demo                   -- runs all 4 sample PRs
      make replay PR=003          -- byte-identical replay
      make counterfactual PR=003 RULE=test-removal
      make ship                   -- emits gh_action.yml for users
