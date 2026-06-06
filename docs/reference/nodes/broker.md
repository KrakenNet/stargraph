# `BrokerNode`

Built-in node (FR-44, FR-46, design §8.1) that calls
`nautilus.Broker.arequest`. The graph-node form of the Nautilus broker
integration: pulls `agent_id` and `intent` off state, resolves the
lifespan-singleton broker, optionally enforces the
`tools:broker_request` capability gate, and patches the response onto state.

## `BrokerNodeConfig`

Pydantic config (subclasses `IRBase`, so `extra="forbid"`). Three string fields
name the state-keys the node reads/writes.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `agent_id_field` | `str` | required | State attribute holding the requesting agent's id (string). |
| `intent_field` | `str` | required | State attribute holding the broker intent string. |
| `output_field` | `str` | required | State key receiving the `BrokerResponse` dump. |

## Constructor

```python
BrokerNode(*, config: BrokerNodeConfig)
```

Single keyword-only arg.

## State contract

- **Reads** — `state.<agent_id_field>` and `state.<intent_field>`; both must
  resolve to `str`.
- **Writes** — `{output_field: <enveloped response dump>}`. The enveloped
  dump is `response.model_dump(mode="json")` plus a `__stargraph_provenance__`
  key carrying `origin=tool`, `source=nautilus`, `external_id=<request_id>`
  per design §8.1, so downstream `stargraph.fathom` mirroring can pick it up.

## Side effects + replay

- `side_effects = SideEffects.read` — Nautilus is read-only from Stargraph's POV
  (Nautilus owns its own write side-effects internally; Stargraph consumes the
  read surface only). Replay-safe by default.

!!! note "Phase 2 carve-out"
    Phase 2 may surface a per-instance override if downstream consumers wire
    Nautilus to side-effecting tools (the design notes this carve-out in §8.1).

## Capabilities

`requires_capabilities = ("tools:broker_request",)` exposes the capability
namespace so callers / tests can introspect the gate. The IR-level enforcement
sits at `stargraph.graph.loop._check_node_capability` (canonical line of defense
from `NodeSpec.required_capability`).

The in-`execute` check is a defense-in-depth backstop for direct dispatch — unit
tests, ad-hoc `stargraph.Graph` builders that don't thread an IR through.
`ctx.capabilities=None` (POC default) skips the in-node gate.

## YAML

```yaml
nodes:
  - id: broker
    kind: broker
state_schema:
  agent_id: str
  intent: str
tools:
  - id: broker_request
    version: "1.0"
```

See `tests/fixtures/triage_stub_broker.yaml` for the full canonical pipeline.

## Errors

- `StargraphRuntimeError` — no lifespan-singleton `Broker` is registered (i.e.
  `current_broker()` returns `None` because the FastAPI lifespan factory has
  not run yet, or the node is being dispatched outside an active lifespan).
- `StargraphRuntimeError` — `agent_id_field` or `intent_field` resolves to
  something other than `str` (`field` attached for diagnostics).
- `CapabilityError` — `ctx.capabilities` is supplied (non-`None`) but does
  not grant `tools:broker_request`.
- `AttributeError` — `state.<agent_id_field>` or `state.<intent_field>`
  missing.

## See also

- [`NodeBase`](base.md) — abstract contract.
- [`WriteArtifactNode`](write-artifact.md), [`InterruptNode`](interrupt.md) —
  same construction convention.
- `stargraph.serve.contextvars.current_broker` — lifespan-singleton resolution.
