# Cassette: `case_8821` (auditor drill fixture)

The hero walkthrough case for the SOC Triage++ demo — a LockBit-style
ransomware detonation on a tier-0 production billing database
(`billing-db-01`). This directory holds the deterministic fixtures the
auditor drill (`../../scripts/replay_drill.sh`) and the graph tests use to
replay the case **without a live LLM** and to demonstrate cryptographic
replay + counterfactual diffing.

## Files

| File | Role |
| --- | --- |
| `llm_responses.json` | Recorded `triage_decide` (dspy) responses keyed by alert **signature**. A mock LM replays these so the LLM disposition/reason are deterministic — same shape as `demos/sentinel_dark_watch/fixtures/llm_responses.json`. |
| `expected_run.json` | Recorded terminal-state snapshot of the `case_8821` run (feature vector, ML risk band, disposition, route, HITL gate, fused priors). The replay asserts a fresh run matches this byte-for-byte. |
| `counterfactual_tier_prod.json` | A `CounterfactualMutation` body (`{step, mutation, reason}`) that overrides `asset_tier=prod` at the fork point — the "what-if this were production?" diff. |

## How the cassette is consumed

`triage_decide` is the only non-deterministic node (it calls an LLM via
dspy). Everything upstream is deterministic by construction:

* **`ingest` (IngestAlert)** — reads the `case_8821` line from
  `data/alerts_sample.jsonl` and builds the fixed feature vector
  `[severity_raw, tier_dev, tier_staging, tier_prod, reputation, hour, repeat]`.
* **`retrieval` (RetrievalPriors)** — RRF over `data/priors/` is a pure
  function of the seeded files.
* **`risk_score` (MLNode, ONNX, sha256-pinned)** — the RandomForest export
  is deterministic; `case_8821` scores `risk=2` (high) with ~0.999 prob.

So to make the **whole** run replayable you only need to pin the LLM step.
Two equivalent ways the drill / tests do that:

1. **Mock LM (preferred for tests):** configure a mock `dspy.LM` that returns
   `llm_responses.json[<signature>]` (falling back to `default`). No network.
2. **Live `stargraph serve` (the sales drill):** if `LLM_BASE_URL` is reachable
   the run uses the real model; the drill then proves replay determinism via
   Stargraph checkpoints + the counterfactual fork, not via the cassette.

The cassette is intentionally a recorded-artifact fixture (not bound to any
private Stargraph replay machinery): `stargraph serve` already gives byte-identical
checkpoint replay and `POST /v1/runs/{id}/counterfactual` for free, so the
fixture only needs to (a) pin the LLM step deterministically and (b) record
the expected terminal state to diff against.

## Expected outcome (`case_8821`)

```
ingest → retrieval → risk_score(=2/high) → triage_decide(=auto_remediate)
       → soc_policy → analyst_gate (HITL: prod + auto_remediate)
       → write_artifact → audit → halt
```

The prod tier + `auto_remediate` disposition is exactly the soc-policy
branch that routes through the analyst gate — so the demo always reaches the
HITL pause on the hero case.
