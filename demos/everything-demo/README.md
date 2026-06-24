# everything-demo

Ultra-complex Stargraph workflow that exercises **one of every Stargraph capability** in a single graph. Use it as a:

- Smoke test for new releases (does my engine still load every node kind, every action variant, every store?)
- Integration test bed when adding a new node type / store provider / trigger
- Reference IR when authoring a new graph and you want to see every block in one place

## Coverage matrix

| Surface | Coverage |
|---------|----------|
| **Node kinds** | `echo`, `passthrough`, `dspy`, `ml`, `retrieval`, `tool`, `broker`, `memory_write`, `write_artifact`, `interrupt`, `subgraph`, plus 3 custom `module:Class` `NodeBase` subclasses |
| **Action variants** (FR-11) | `goto`, `halt`, `parallel`, `retry`, `assert`, `retract`, `interrupt` |
| **Stores** | vector (LanceDB), graph (RyuGraph), doc (SQLite), memory (SQLite), fact (SQLite) |
| **Triggers** | manual, cron (`0 9 * * *` America/New_York), webhook (HMAC + nonce LRU) |
| **Adapters** | DSPy (force-loud) for `extract_intent` + `summarize_resolution`; MCP for `notify_user_call` |
| **Governance packs** | `stargraph.bosun.budgets`, `stargraph.bosun.audit`, `stargraph.bosun.safety_pii`, `stargraph.bosun.retries`, `demo.routing`, `demo.safety` |
| **Skills** | one ReactSkill-shaped `demo.triage@1.0.0` bundle in `skills/` |
| **Tools** | `demo.lookup_history@1.0.0` (read), `demo.notify_user@1.0.0` (external) |
| **Capabilities** | 10 capability strings declared at IR level for engine-side default-deny |
| **Checkpointer** | `every: node-exit`, sqlite-backed → enables full replay |
| **HITL** | one durable `interrupt` (timeout: null) + branch on response decision |
| **Provenance** | `__stargraph_provenance__` envelope on every tool-shaped node + parent_run_id for cross-graph spawn (when implemented) |

## Domain

Support-ticket triage and remediation. Three trigger paths:

1. **Webhook** — POST to `/triggers/support` with HMAC-signed body, fires a fresh run per ticket.
2. **Cron** — daily at 09:00 NYC, fires a digest run.
3. **Manual** — `stargraph run` or `POST /v1/runs` for replay/repair.

Pipeline:

```text
intake (echo)
  └─► extract_intent (dspy)
        └─► search_kb (retrieval, fan-out vector+graph+doc → RRF)
              └─► risk_score (ml, sklearn classifier)
                    └─► lookup_history (custom NodeBase wrapping @tool)
                          └─► broker_compliance_check (broker, retry-able)
                                └─► [parallel: enrichment subgraph + memory_write]
                                      └─► assert compliance fact
                                            └─► hitl_review (interrupt, durable)
                                                  └─► branch_response (custom)
                                                        ├─[approve]─► retract draft fact
                                                        │              └─► notify_user (mcp tool)
                                                        │                    └─► summarize_resolution (dspy)
                                                        │                          └─► persist_report (write_artifact)
                                                        │                                └─► complete (echo, halt)
                                                        └─[reject ]─► halt
```

## Files

```
demos/everything-demo/
├── README.md                  # this file
└── graph/
    ├── stargraph.yaml            # main IR (covers all node kinds + all action variants)
    ├── state.py               # Pydantic RunState (every field every node touches)
    ├── tools.py               # @tool-decorated lookup_history (read) + notify_user (external)
    ├── nautilus.yaml          # broker source config (SOC SoR + compliance KB sources)
    ├── triggers.yaml          # one manual + one cron + one webhook spec
    ├── nodes/
    │   └── __init__.py        # StartSentinel, BranchResponse, LookupHistoryCaller (custom NodeBase)
    ├── subgraphs/
    │   └── enrichment.yaml    # SubGraphNode body
    ├── packs/
    │   ├── routing/pack.yaml  # custom Bosun routing pack
    │   └── safety/pack.yaml   # custom Bosun governance/safety pack
    ├── skills/
    │   └── triage_skill.yaml  # demo.triage@1.0.0 manifest
    └── tests/
        └── test_smoke.py      # structural smoke tests (no engine boot required)
```

## Running

### Structural smoke

```bash
cd /path/to/stargraph
uv run pytest demos/everything-demo/graph/tests/ -v
```

Validates every claim in the coverage matrix above.

### End-to-end (CLI, no serve)

```bash
uv run stargraph run demos/everything-demo/graph/stargraph.yaml \
    --checkpoint /tmp/everything.sqlite \
    --inputs '{"trigger_kind":"manual","ticket_id":"T-0001","ticket_text":"can you reset my password","ticket_source":"chat"}'
```

The HITL gate will exit cleanly to `awaiting-input`; resume with:

```bash
uv run stargraph respond <run-id> \
    --action approve --actor analyst@example.com \
    --checkpoint /tmp/everything.sqlite
```

### Serve mode (all three triggers active)

```bash
# 1. point Stargraph at this graph + nautilus.yaml
export STARGRAPH_CONFIG_DIR="$(pwd)/demos/everything-demo/graph"
export STARGRAPH_WEBHOOK_SECRET_CURRENT="$(openssl rand -hex 32)"

# 2. start serve (loads triggers.yaml, builds Broker from nautilus.yaml)
uv run stargraph serve --db /tmp/everything.sqlite

# 3. fire a webhook run from another shell
ts=$(date +%s)
body='{"ticket_id":"T-0002","ticket_text":"VPN dropping every 30s","ticket_source":"email","trigger_kind":"webhook"}'
sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$STARGRAPH_WEBHOOK_SECRET_CURRENT" -binary | xxd -p -c 256)
curl -X POST http://localhost:8000/triggers/support \
    -H "X-Stargraph-Timestamp: $ts" \
    -H "X-Stargraph-Signature: $sig" \
    -H 'Content-Type: application/json' \
    -d "$body"
```

### Replay

After any run completes, replay it bit-identically:

```bash
uv run stargraph replay <run-id> --checkpoint /tmp/everything.sqlite --diff
```

`must_stub` tools (`notify_user`) are stubbed automatically; `recorded_result` tools (`lookup_history`) replay from the cassette. `read`-only nodes re-execute deterministically.

## Known limits

This demo is a **scaffold**, not a production graph:

- DSPyNode bodies stub their LM calls (no real model wired). Replace `extract_intent` / `summarize_resolution` with concrete DSPy modules to drive a real LM.
- MLNode `risk_score` expects an ONNX or sklearn model at a `file://` URI you supply; demo yaml leaves the path blank for the smoke test to skip.
- BrokerNode requires a live `nautilus.yaml` + `stargraph serve`. The `stargraph run` CLI path raises `StargraphRuntimeError("Broker not initialized")` on the broker step (intentional — that is the documented contract).
- `notify_user_call` is the MCP integration seam. Wire it via `stargraph.adapters.mcp.bind` against an MCP stdio server before relying on it; the in-process `@tool` callable is the fallback.
- Custom nodes register via the `module:ClassName` IR `kind:` escape hatch — there is no `stargraph.nodes` entry-point group yet (see `TODO.md`).

## What this exercises that the other demos do not

- `memory_write` node.
- Explicit `assert` and `retract` action variants in the same graph.
- All three triggers wired in one `triggers:` block.
- All five store kinds in one `stores:` block.
- A custom `NodeBase` subclass loaded via the `module:ClassName` import path.
- A standalone Skill manifest under `skills/`.
