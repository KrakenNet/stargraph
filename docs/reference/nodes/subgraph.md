# `SubGraphNode`

Executes a child graph of [`NodeBase`](base.md) instances inside the parent
run's execution context (FR-7, design §3.9.4). A sub-graph is **not** a new IR
construct — it is a node whose body runs a child graph sharing the parent's
event bus, `run_id`, and checkpointer.

Two modes, chosen by the child IR:

- **Sequential** (child has no live-routable rules): the children run once,
  in order — the original FR-7 shape.
- **Rule-routed** (child has rules): the child rules compile via
  `stargraph.fathom.build_ir_routing` into a **child-owned Fathom engine**
  (isolated CLIPS memory — child facts never leak into the parent's engine),
  and the node drives an internal goto/halt loop. This is what lets a shipped
  bundle (judge loop, triage router) run as one node of a parent graph.

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

## Rule-routed mode

Each tick: execute the current child → mirror the child state into the
child's CLIPS engine → evaluate → translate the decision. `Goto` jumps,
`Halt` projects outputs and returns, no action continues to the next
declared child (or returns at the end). The loop is bounded by
`config.max_steps` (default 50) and exhausting it is a loud error, never a
silent truncation. One `TransitionEvent` per tick carries the decision
`reason`. `interrupt` and `parallel` inside a routed child fail loudly (v1).

### I/O projection — `SubGraphNodeConfig`

Config is only valid in rule-routed mode (projection without routing is
dead wiring, rejected at graph load):

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `inputs` | `dict[str, str]` | `{}` | entry: `{child_field: parent_field}` |
| `outputs` | `dict[str, str]` | `{}` | exit: `{parent_field: child_field}` |
| `max_steps` | `int` (1–10 000) | 50 | internal tick bound |

Unmapped fields project by shared name in both directions; child-only
fields never leak into the parent; a mapped-but-missing field is a loud
error.

## State contract

- **Reads** — whatever each child reads (plus the `inputs` projection).
- **Writes** — sequential mode: cumulative dict of child outputs
  (last-write-wins on key collisions); routed mode: the `outputs`
  projection (or shared-name fields) of the final child state. The parent
  loop applies the result with a single `state.model_copy(update=outputs)`.

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

Rule-routed with projection (`spec` is the child IR path, resolved relative
to the parent IR's directory):

```yaml
nodes:
  - id: refine
    kind: subgraph
    spec: bundles/evaluator_optimizer/graph.yaml   # child IR with rules
    config:
      inputs: {task: request}      # child.task <- parent.request
      outputs: {result: answer}    # parent.result <- child.answer
      max_steps: 20
```

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
