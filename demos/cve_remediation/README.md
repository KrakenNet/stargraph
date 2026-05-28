# cve_remediation — Harbor production showcase

Full-stack CVE remediation pipeline. Doctrine-grounded ingest → SSVC tiering
→ multi-runtime sandbox → progressive execute → retrospective.

This is the **master-of-all** Harbor demo. It exercises every node kind,
action variant, store kind, trigger kind, and HITL pattern Harbor ships,
plus the Phase 0 (doctrine ingest) and Phase 6 (offline GEPA learning)
loops — and the orthogonal safety block (drift watch, tier re-eval,
audit anchor, lab-leak reaper, rolling restart).

## Why this demo

The CVE pipeline is the use case Harbor was built for: a long-running,
LLM-touched, human-gated, regulated-industry workflow with multiple
external systems, durable state, and a non-negotiable audit chain.
Everything Harbor ships earns its place in this graph.

## At a glance

| Metric | Value |
|---|---|
| IR YAMLs | 10 (1 main + 2 phase + 2 subgraph + 5 triggered) |
| Total nodes | 140 stub-wired across 8 kinds |
| Rule firings (simulate) | 180 across all IRs |
| Fathom packs | 5 (1 routing + 4 governance) |
| Triggers | 9 (2 manual / 1 webhook / 6 cron) |
| HITL gates | 4 durable (timeout=null) |
| Tests | 174 |

## Run the demo

```bash
# Inspect every IR (rule-firing trace, no side effects)
uv run --no-project python -m demos.cve_remediation.run_demo

# Run the full test suite (174 tests)
uv run --no-project python -m pytest demos/cve_remediation/graph/tests --no-cov

# JSON output (machine-parseable for CI)
uv run --no-project python -m demos.cve_remediation.run_demo --json
```

## Run the watcher UI

The watcher is a browser-based run visualizer served by the same
FastAPI process. It works in two modes: **simulated** (baked-in demo
data, no server needed beyond static files) and **live** (streams
real run events over WebSocket).

```bash
# Start the server (serves both the API and the watcher UI)
uv run --no-project python -m demos.cve_remediation.serve_cve_rem

# Open the watcher
#   Simulated demo (no live run required):
#     http://localhost:9000/watch/?demo=1
#
#   Live run (after POST /v1/runs):
#     http://localhost:9000/watch/?run=<run_id>
```

### Simulated mode (`?demo=1`)

Plays a pre-baked CVE-2021-44228 (Log4Shell) remediation run with
realistic timing. Every node has a specialized detail view — CVSS
severity, SSVC decision matrix, sandbox proof pipeline, etc. Good
for demos and UI development; no LLM or external services needed.

### Live mode (`?run=<run_id>`)

Streams real node events from a running graph execution. Start a run
via the API, then open the watcher with the returned `run_id`:

```bash
# In another terminal — kick off a live run
curl -X POST http://localhost:9000/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"graph_id": "cve-rem-pipeline", "input": {"cve_id": "CVE-2021-44228"}}'

# Response includes run_id — paste it into the watcher URL
```

The watcher connects to the `/v1/runs/{run_id}/stream` WebSocket and
renders each node's specialized view as events arrive. Checkpoint
state is fetched from `/watch/api/run/{run_id}/checkpoints` for
per-node state diffs.

### Environment

The server reads `.env` in the demo directory. For live runs with
real LLM nodes, set:

```
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=no-key
```

## Layout

```
cve_remediation/
  cve-rem-graph.md            Definitive design (v6). Phase 0..6 + safety block.
  cve-rem-pipeline.md         Original prose pipeline notes.
  pipeline-graph.md           Earlier v5 draft (kept for reference).
  run_demo.py                 End-to-end runner — drives every IR through harbor.
  graph/
    harbor.yaml               Main pipeline IR (Phase 1..5, steps 1-18).
    state.py                  CveRemState + 5 triggered-graph state classes.
    nodes.py                  POC stub NodeBase per Harbor kind.
    triggers.yaml             9 triggers binding 8 graphs.
    tool-id-mapping.md        Master canonical-broker tool-id table.
    phase0/doctrine_ingest.yaml          Phase 0: doctrine + boot-time integrity.
    phase6/offline_learning.yaml         Phase 6: GEPA compile + Shamir ship.
    subgraphs/
      sandbox_dispatch.yaml              Step 11 — branched sandbox runtime.
      progressive_execute.yaml           Step 13 — canary → stage → fleet.
    triggered/
      drift_watch.yaml                   24-72h passive drift sweep.
      tier_re_eval.yaml                  Daily SSVC re-fire on TRACK/DEFER.
      audit_anchor.yaml                  Daily Nautilus JWS chain anchor.
      lab_leak_reaper.yaml               Hourly CargoNet lab TTL sweep.
      rolling_restart.yaml               Worker rollout post-Shamir ship.
    rules/
      cve_rem.routing/                   8 routing rules (YAML flavor).
      cve_rem.kill_switches/             8 CLIPS kill-switch rules.
      cve_rem.doctrine_trust/            4 CLIPS trust rules.
      cve_rem.offline_isolation/         4 CLIPS isolation rules.
      cve_rem.gepa_score_policy/         5 CLIPS score-policy rules.
    tests/
      test_smoke.py                      Structural IR invariants.
      test_packs.py                      Pack manifest invariants.
      test_pack_*.py                     CLIPS round-trip per pack.
      test_tool_ids.py                   Canonical-broker invariant.
      test_node_stubs.py                 Stub NodeBase contract tests.
      test_triggers.py                   Trigger config invariants.
      test_e2e.py                        End-to-end harbor run --inspect.
```

## Capability matrix

| Capability | Where it lands |
|---|---|
| Every node kind | 8 stubs in `graph/nodes.py` (passthrough/tool/broker/write_artifact/interrupt/ml/dspy/subgraph) |
| All action variants | goto, halt, parallel (3×), retry, assert, retract, interrupt — exercised across IRs |
| 5 store kinds | `stores: []` placeholder; Phase E binds vector/graph/doc/memory/fact |
| 3 trigger kinds | `graph/triggers.yaml`: 2 manual + 1 webhook + 6 cron |
| Durable HITL | 4 gates with `timeout: null` (ingest / plan / change / retro) |
| Field-merge registry | All `state.py` classes are flat at top level |
| State-class escape hatch | Every IR uses `state_class:` (not the dict-form `state_schema`) |
| Pack mounts | 6 packs in main (4 stock Bosun + 2 demo-custom) |
| Multi-runtime sandbox | `subgraphs/sandbox_dispatch.yaml` — cargonet_lab / docker_compose / static_detection / skip |
| Progressive rollout | `subgraphs/progressive_execute.yaml` — canary → stage → fleet with health gates |
| Doctrine ingest (Phase 0) | Idempotent corpus pin + Ed25519 manifest sign + allowlist update |
| Offline learning (Phase 6) | GEPA weighted score + 2-of-3 Shamir ship + rolling restart |
| Audit anchor | Daily JWS chain head publication; 24h page / 72h halt-new |
| Drift watch | 24-72h passive sweep spawning child runs on signature match |
| Tier re-eval | TRACK/DEFER pairs re-evaluated daily; spawns main on escalation |
| Lab-leak reaper | Hourly TTL sweep of CargoNet labs |

## Phase E roadmap (real bodies, not stubs)

The current scaffold is POC: every node returns `{}` (or, for the
HITL stub, a synthetic auto-approve). Phase E swaps stubs for:

- **E1** — Real node implementations per phase (DSPy planner/critic/render,
  ML tier scorer, Nautilus broker invocation, real artifact writes).
- **E2** — Pack JWT signing via the krakntrust dev key.
- **E3** — Real Nautilus broker-intent payloads (live ServiceNow / pgvector /
  pagerduty routes via the canonical `nautilus.broker_request@1`).

Each E-task is independently shippable and gated by green tests in this
package.
