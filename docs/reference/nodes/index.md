# Node Reference

Every executable graph node subclasses [`NodeBase`](base.md) and implements
`async execute(state, ctx) -> dict[str, Any]`. The execution loop merges the
returned dict into the next state via the field-merge registry (FR-11) — nodes
never mutate state in place.

## Catalog

| Kind | Class | Purpose | Side-effects | Replay |
| --- | --- | --- | --- | --- |
| [`echo`](base.md#echonode) | `EchoNode` | Fixture: copies `state.message` through. | `none` | re-execute |
| [`dspy`](dspy.md) | `DSPyNode` | Wraps a DSPy module behind the force-loud JSON adapter. | `external` (LLM call) | `must-stub` (cassette) |
| [`ml`](ml.md) | `MLNode` | Runs a sklearn / xgboost / onnx classical-ML model. | `none` | re-execute |
| [`memory`](memory.md) | `MemoryWriteNode` | Persists an `Episode` into a `MemoryStore`. | `write` | `must-stub` |
| [`retrieval`](retrieval.md) | `RetrievalNode` | Parallel fan-out over stores with RRF fusion. | `read` | re-execute |
| [`subgraph`](subgraph.md) | `SubGraphNode` | Executes a child sequence inside the parent run. | inherited | inherited |
| [`write_artifact`](write-artifact.md) | `WriteArtifactNode` | Persists a state-resident payload through `ArtifactStore`. | `write` | `must_stub` / `fail_loud` |
| [`interrupt`](interrupt.md) | `InterruptNode` | Bypass-Fathom HITL pause primitive. | `read` | re-execute (loop owns) |
| [`broker`](broker.md) | `BrokerNode` | Calls `nautilus.Broker.arequest`. | `read` | re-execute |

The `kind` strings above are the IR-level node-factory keys; the registry resolves
each one to a concrete class. See [`stargraph.cli.run._NODE_FACTORIES`][cli-factories]
for the POC `echo` / `halt` / `dspy` path and `stargraph.serve.lifecycle` for the
production registration table.

## Side-effect classification

Nodes advertise their side-effect class through `SideEffects` and their replay
posture through `ReplayPolicy`. The IR loader threads these through the runtime
gate (FR-33). See [IR Schema Reference](../ir-schema.md) for the wire format.

## See also

- [`NodeBase`](base.md) — the abstract contract every node implements.
- [Engine: replay](../../engine/replay.md) — how replay routes side-effecting nodes.
- [IR Schema](../ir-schema.md) — IR-level `NodeSpec` shape.

[cli-factories]: ../python/index.md
