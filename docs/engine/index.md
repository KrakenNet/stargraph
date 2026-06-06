# Engine Subsystems

Stargraph's engine is the runtime that executes a validated `Graph` to a terminal
`RunSummary`. This page is the orienting map: each subsystem owns one
responsibility and exposes a single Python entry point.

## Subsystem map

| Module | Responsibility | Public surface |
|---|---|---|
| `stargraph.graph` | Static, hashable, IR-validated graph definition + async run handle | `Graph`, `GraphRun`, `RunState`, `structural_hash`, `runtime_hash` |
| `stargraph.checkpoint` | Per-step persistence contract + drivers | `Checkpoint`, `Checkpointer`, `RunSummary` |
| `stargraph.runtime` | Loop, event bus, mirror lifecycle, reducer registry | (driven internally by `GraphRun.start()`) |
| `stargraph.nodes` | Built-in node implementations | `NodeBase`, `MLNode`, `DSPyNode`, `SubgraphNode` |
| `stargraph.replay` | Cassettes, comparison, counterfactual fork | `ToolCallCassette`, `CounterfactualMutation`, `derived_graph_hash` |
| `stargraph.fathom` | CLIPS-backed governance / rule firing | `FathomAdapter` |
| `stargraph.errors` | Force-loud error hierarchy (FR-6) | `StargraphError` and subclasses |

Construction is synchronous and side-effect free; execution is async and
single-use per `GraphRun`. The `(graph_hash, runtime_hash)` pair is pinned at
`Graph.__init__` and travels with every `Checkpoint` so a resume can refuse a
mismatch loudly (FR-20).

## Graph / GraphRun

`Graph` is the sync construction half of the Temporal-style split. It validates
the IR, compiles the `state_schema` into a Pydantic `BaseModel` subclass, and
pins the JCS structural hash plus the runtime hash:

```python
from stargraph.graph import Graph
from stargraph.ir._models import IRDocument

ir = IRDocument(ir_version="1.0.0", id="run:hello", nodes=[])
graph = Graph(ir)

graph.graph_hash    # 64-char hex sha256 of the JCS-canonical IR
graph.runtime_hash  # sha256(python_version + stargraph_version)
```

`GraphRun` is the async execution half — single-use, one `run_id` per handle:

```python
from stargraph.checkpoint.sqlite import SQLiteCheckpointer

checkpointer = SQLiteCheckpointer("checkpoints.db")
await checkpointer.bootstrap()

run = await graph.start(checkpointer=checkpointer)
summary = await run.start()        # drive to terminal state
async for event in run.stream():   # observe transitions
    ...
```

Lifecycle states
(`pending|running|paused|awaiting-input|done|cancelled|error|failed`)
are exposed on `run.state` for inspection but transitioned only by the
run loop.

The `awaiting-input` state is reached when a node raises
`InterruptAction`. The loop emits `WaitingForInputEvent`, transitions
state, and **returns** — it does not poll for a transition back to
`running`. Resume is **cold-restart-only** in v1: stop the process,
call `GraphRun.respond(...)` (which flips state and asserts the
response as a `stargraph.evidence` Fathom fact), then restart with
`GraphRun.resume(checkpoint)`. Warm in-process resume is on the
post-1.0 roadmap; see [v1 limits](../reference/v1-limits.md) for the
boundary list.

See:

- [Checkpointer Protocol](checkpointer.md)
- [Replay tutorial](replay.md)
- [Counterfactual forks](counterfactual.md)

## MLNode

`MLNode` (`stargraph.nodes.MLNode`) wraps the loaders in `stargraph.ml.loaders` so a
sklearn / xgboost / onnx model can be dropped into a graph as a normal node.
Construction is eager-validated — the runtime is checked, the pickle gate fires
for sklearn when `allow_unsafe_pickle=False`, and the ONNX session warms via the
shared cache:

```python
from stargraph.nodes.ml import MLNode

# ONNX is the recommended runtime — no pickle, no untrusted deserialization.
node = MLNode(
    model_id="risk-classifier",
    version="1.4.0",
    runtime="onnx",
    file_uri="file:///opt/models/risk-1.4.0.onnx",
    expected_sha256="2c1f...e0ba",  # pin the model bytes
    input_field="features",
    output_field="risk_score",
)
```

Sklearn requires `allow_unsafe_pickle=True` (default-deny per FR-30 antipattern
guard #4); xgboost loads from JSON/UBJ and ignores the pickle gate. Inference
is offloaded to a worker thread via `asyncio.to_thread`, so the event loop is
never blocked by a sync `predict` call.

## Force-loud errors

The engine never silently coerces on schema mismatch, missing checkpoint, or
counterfactual hash collision. All structured failures inherit from
`StargraphError` and carry typed context fields you can assert against:
`CheckpointError(reason="graph-hash-mismatch", expected_hash=..., actual_hash=...)`.

Catch the narrow subclass; the context fields are part of the public contract.
