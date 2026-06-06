# `SubGraphNode`

Executes a child sequence of [`NodeBase`](base.md) instances inside the parent
run's execution context (FR-7, design §3.9.4). A sub-graph is **not** a new IR
construct — it is a node whose body runs a child sequence sharing the parent's
event bus, `run_id`, and checkpointer.

## Constructor

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `subgraph_id` | `str` | required | Stable identifier stamped onto every child event's `branch_id` field. Conventionally matches the parent `NodeSpec.id` so the lineage line is searchable. |
| `children` | `list[NodeBase]` | required | Ordered list of child nodes to dispatch. Empty list is legal (degenerate sub-graph: no events, no merges). |

Both keyword-only.

## Provenance lineage

- Child events carry `run_id == parent.run_id` (the parent's identity propagates
  verbatim — no new `run_id` is minted; FR-7 treats the sub-graph as a logical
  fragment of the parent run).
- Child events carry `branch_id == subgraph_id`; the parent's own events carry
  `branch_id is None`. The two are interleaved on the same bus.

Per child, a `TransitionEvent` is published on the parent's bus with:

- `run_id` = parent `ctx.run_id`,
- `branch_id` = `self.subgraph_id`,
- `from_node` = child id, `to_node` = next child id (or `""` on the terminal
  child to mirror the parent loop's convention),
- `rule_id = ""`, `reason = "subgraph"`.

## Required context — `SubGraphContext`

`ctx` must satisfy this `runtime_checkable` Protocol:

| Field | Type |
| --- | --- |
| `run_id` | `str` |
| `bus` | `Any` (must expose `async send(event, *, fathom=...)`) |
| `fathom` | `Any` (optional `FathomAdapter`) |

The real `stargraph.graph.run.GraphRun` satisfies this surface; tests pass
duck-typed contexts.

## State contract

- **Reads** — whatever each child reads.
- **Writes** — cumulative dict of child outputs (last-write-wins on key
  collisions). The parent loop applies the result with a single
  `state.model_copy(update=outputs)`.

## Side effects + replay

Inherited from the children — `SubGraphNode` itself emits transition events
(read-only on the bus). Replay posture follows the children.

## YAML

```yaml
nodes:
  - id: train_subgraph
    kind: subgraph
    spec:
      tool: ml.fit
      inputs:
        n_samples: 64
        n_features: 4
        random_state: 0
      outputs:
        artifact_path: artifact_path
        content_hash: content_hash
```

See `tests/fixtures/training-subgraph.yaml` for the design §3.9.4 reference
recipe (training-as-subgraph).

## Errors

- `AttributeError` — `ctx` does not satisfy `SubGraphContext` (missing
  `run_id`, `bus`, or `fathom`). Surfaces as a wiring bug at the call site
  rather than silently dropping events (FR-6 force-loud).
- Any error raised by a child propagates verbatim; sibling children later in
  the list are not run.

## See also

- [`NodeBase`](base.md) — abstract contract.
- [`MLNode`](ml.md) + [`WriteArtifactNode`](write-artifact.md) — typical child
  composition.
