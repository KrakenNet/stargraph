# `InterruptNode`

Bypass-Fathom HITL pause primitive (FR-82, AC-14.2, design §9.2). On dispatch
the node raises the loop's typed control-flow signal carrying an
`InterruptAction` payload; the loop catches the signal, transitions
`state="awaiting-input"`, emits a `WaitingForInputEvent`, persists a
checkpoint, and exits cleanly. Resume happens via cold-restart through
`GraphRun.resume` after `GraphRun.respond` flips state back to `"running"`
(design §9.4).

!!! note "Interrupt is control-flow, not routing"
    Per design §17 Decision #1 (locked), interrupt is a control-flow primitive,
    not a routing decision: the node deliberately does **not** return an
    `InterruptAction`-shaped patch via the field-merge surface. The dispatch
    path is the typed signal raise — `RoutingDecision` never carries an
    interrupt variant.

## `InterruptNodeConfig`

Pydantic config (subclasses `IRBase`, so `extra="forbid"`). Fields mirror
`harbor.ir._models.InterruptAction` verbatim.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `prompt` | `str` | required | Analyst-facing prompt surfaced via WS / `GET /runs/{id}`. |
| `interrupt_payload` | `dict[str, Any]` | `{}` | Opaque blob exposed via WS / respond endpoint. |
| `requested_capability` | `str \| None` | `None` | Capability the human responder must hold; gate enforced at respond-time, not here. |
| `timeout` | `timedelta \| None` | `None` | Wait bound; `None` = wait indefinitely (FR-87, NFR-22). |
| `on_timeout` | `Literal["halt"] \| str` | `"halt"` | Loop policy on timeout: `"halt"` (terminal) or `"goto:<node_id>"` (resume target). |

## Constructor

```python
InterruptNode(*, config: InterruptNodeConfig)
```

Single keyword-only arg; the config is attached at construction time — same
convention [`WriteArtifactNode`](write-artifact.md) and [`BrokerNode`](broker.md)
established.

## State contract

- **Reads** — none; the signal is raise-only and the loop owns state.
- **Writes** — none from this node. The actual analyst response is asserted as
  a `harbor.evidence` Fathom fact by `GraphRun.respond` post-resume.

## Side effects + replay

- `side_effects = SideEffects.read` — no Harbor-side mutation; only requests
  human input.
- Replay re-executes the raise; the loop's `_HitInterrupt` handler is
  deterministic.

## YAML

```yaml
nodes:
  - id: human_gate
    kind: interrupt
rules:
  - id: r-gate-to-branch
    when: "?n <- (node-id (id human_gate))"
    then:
      - kind: interrupt
        prompt: "Approve remediation for {record_id}?"
        interrupt_payload:
          requested_capability: "runs:respond"
        requested_capability: "runs:respond"
        timeout: "PT5M"
        on_timeout: "halt"
```

The `kind: interrupt` rule action mirrors `InterruptNodeConfig` field-by-field.
See `tests/fixtures/triage_stub_broker.yaml` for the full HITL pipeline.

## Errors

- `_HitInterrupt` (private to `harbor.graph.loop`) is **always** raised — this
  is the dispatch contract, not an error condition.

## See also

- [`NodeBase`](base.md) — abstract contract.
- `harbor.graph.run.GraphRun.respond` / `GraphRun.resume` — the resume path.
- [Engine: checkpointer](../../engine/checkpointer.md) — durability for HITL waits.
