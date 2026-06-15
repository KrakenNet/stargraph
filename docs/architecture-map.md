# Architecture map

Where everything lives. One line per package, the symbols you actually edit,
and the test marker that covers it. For the *why*, see
[`design-docs/`](https://github.com/KrakenNet/stargraph/tree/main/design-docs).

## Data flow

A run moves through the engine like this:

1. **`Graph.start(ir, state, ...)`** builds a `GraphRun` (sync construction,
   side-effect free; validates IR + computes `structural_hash`).
2. **`GraphRun.stream()`** drives the loop, yielding `Event`s over an `EventBus`.
3. Each step `loop.execute()` calls **`NodeBase.execute(state, ctx)`**. Nodes do
   work (echo, DSPy/LLM, ML, tool, retrieval, sub-graph).
4. On node exit, fields marked **`Annotated[T, Mirror(...)]`** are mirrored into
   CLIPS via `FathomAdapter.mirror_state()` (off-thread; CLIPS is sync).
5. **Fathom rules** fire over the asserted facts and emit `stargraph_action`
   facts (`goto`/`halt`/`parallel`/`retry`/`assert`/`retract`/`interrupt`).
6. The adapter translates actions to a routing decision → `TransitionEvent`.
7. A **`Checkpoint`** is persisted by the `Checkpointer`. Loop continues until
   `halt` or error → `ResultEvent` + `RunSummary`.
8. `serve` exposes all of this over HTTP/WS; `replay` re-runs deterministically
   from any checkpoint with mutated facts/nodes/rules.

**The boundary that breaks first for newcomers:** you mutate Pydantic `state`
freely *inside* a node. Only `Mirror`-annotated fields cross into CLIPS, and
only on node exit. Rules read facts; they never touch Python state directly.

## Core packages (`src/stargraph/`)

| Package | Owns | Key symbols | Marker |
|---|---|---|---|
| `graph` | Graph definition, run handle, structural hashing | `Graph`, `GraphRun`, `RunState`, `structural_hash` | unit, integration |
| `nodes` | Node interface + concrete node kinds | `NodeBase`, `EchoNode`, `DSPyNode`, `SubGraphNode`, `ExecutionContext` | unit, integration |
| `runtime` | Event vocabulary, bus, action dispatch | `Event`, `EventBus`, `TransitionEvent`, `ToolCallEvent`, `ResultEvent` | unit |
| `fathom` | CLIPS rule adapter, provenance, action extraction | `FathomAdapter`, `mirror_state`, `stargraph_action` deftemplate | unit, integration |
| `ir` | IR models, canonical dumps/loads, `Mirror` lifecycle, validate | `IRDocument`, `NodeSpec`, `RuleSpec`, `Mirror`, `dumps`, `loads`, `validate` | unit |
| `checkpoint` | Checkpointer Protocol + records | `Checkpointer`, `Checkpoint`, `RunSummary` | unit, integration |
| `stores` | Store Protocols + 5 backends | `VectorStore`, `GraphStore`, `DocStore`, `MemoryStore`, `FactStore`, `SQLite*`, `LanceDBVectorStore` | unit, knowledge, integration |
| `replay` | Cassettes, counterfactual mutation, determinism | `ToolCallCassette`, `CounterfactualMutation`, `derived_graph_hash` | unit, integration |
| `skills` | Skill base, taxonomy, ReAct tool-loop | `Skill`, `SkillKind`, `ReactSkill` | unit, knowledge |
| `tools` | Tool decorator + spec | `tool`, `ToolSpec`, `SideEffects`, `ReplayPolicy` | unit |
| `registry` | In-memory tool/skill/store registry | `ToolRegistry`, `StoreRegistry`, `Tool` | unit |
| `security` | Capability-based access control | `Capabilities`, `CapabilityClaim` | unit |
| `plugin` | Pluggy hookspecs + loader | `hookspec`, `hookimpl`, `build_plugin_manager` | unit, integration |
| `triggers` | Trigger Protocol, dispatcher (manual/cron/webhook) | `Trigger`, `TriggerEvent`, `dispatch_trigger_lifecycle` | unit, trigger, scheduler |
| `serve` | HTTP/WS API, auth, history, scheduler | `serve/api.py`, `serve/auth.py`, `serve/scheduler.py` | serve, api, websocket |
| `bosun` | Governance rule packs, signing | `bosun/signing.py`, pack dirs (`retries`, `budgets`, `safety_pii`, `audit`, `shipwright`) | serve |
| `cli` | Typer CLI | `app`, `main`; commands: `run`, `inspect`, `simulate`, `counterfactual`, `replay`, `respond`, `serve` | unit |

## Supporting packages

| Package | Owns | Marker |
|---|---|---|
| `artifacts` | Artifact store protocol + refs (`ArtifactStore`, `ArtifactRef`) | unit |
| `audit` | JSONL append-only audit sink (`JSONLAuditSink`) | unit |
| `adapters` | DSPy + MCP seams | unit, integration |
| `ml` | Model loaders (`load_sklearn_model`, `load_xgboost_model`, `get_onnx_session`) | unit |
| `config` | Boot-time config loaders (`load_triggers`) | unit |
| `logging` | structlog + ContextVar correlation (`get_logger`, `run_context`) | unit |
| `schemas` | IR JSON Schema paths/URLs (`schema_path`, `schema_url`) | unit |
| `errors` | Exception hierarchy (`StargraphError` + subclasses) | unit |
