# `NodeBase` and `ExecutionContext`

The abstract base class plus the per-run `Protocol` every node receives.

## `NodeBase`

Abstract base class for every executable graph node (FR-1). Concrete subclasses
implement `async execute(state, ctx)` and return a dict keyed by state-field
name; the execution loop merges each returned dict into the next state via the
field-merge registry (FR-11). Nodes never mutate state in place.

```python
from stargraph.nodes import NodeBase

class MyNode(NodeBase):
    async def execute(self, state, ctx):
        return {"output_field": ...}
```

`NodeBase` itself carries no instance state, so subclasses can be plain classes,
dataclasses, or `pydantic.BaseModel` subclasses depending on their validation
needs. Subclasses are free to declare additional fields (e.g. `model_id` on
[`MLNode`](ml.md)).

### `execute(state, ctx)`

| Param | Type | Description |
| --- | --- | --- |
| `state` | `pydantic.BaseModel` | Current run state — immutable inside the call. |
| `ctx` | `ExecutionContext` | Per-run execution context (see below). |
| **returns** | `dict[str, Any]` | Patch keyed by state-field name; merged via FR-11. |

## `ExecutionContext`

Minimal per-run context surfaced to nodes — a `runtime_checkable`
`typing.Protocol`. Phase-1 only pins `run_id`. Concrete contexts (the real
`stargraph.graph.run.GraphRun`) attach more fields (event sink, replay flag,
artifact store, capability gate); nodes that need those fields declare their
own narrower Protocol — see [`SubGraphContext`](subgraph.md) and
[`WriteArtifactContext`](write-artifact.md).

```python
@runtime_checkable
class ExecutionContext(Protocol):
    run_id: str
```

!!! note "Why a Protocol"
    Structural typing keeps the contract stable as later phases attach new
    context fields (capabilities gate, event sink, replay flag, mirror handle,
    checkpointer). Nodes that don't read a context field can ignore the
    parameter entirely.

## `EchoNode`

No-op fixture node — copies `state.message` straight through to the same field.
Used by `tests/fixtures/sample-graph.yaml` to exercise the dispatch + merge path
without depending on any tool, adapter, or external service. Missing `message`
raises `AttributeError` at runtime to surface fixture mis-configuration loudly.

```yaml
nodes:
  - id: node_a
    kind: echo
state_schema:
  message: str
```

## State contract

- **Reads** — nothing by contract; concrete subclasses declare what they read.
- **Writes** — the dict returned by `execute`. By convention, output keys live
  on `self.output_field` (see `MLNode`, `WriteArtifactNode`, `BrokerNode`).
- **Merge semantics** — last-write-wins on key collisions, per the field-merge
  registry (FR-11).

## See also

- [`DSPyNode`](dspy.md), [`MLNode`](ml.md), [`SubGraphNode`](subgraph.md) — concrete subclasses.
- [IR Schema](../ir-schema.md) — `NodeSpec.kind` resolution.
