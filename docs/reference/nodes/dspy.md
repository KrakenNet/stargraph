# `DSPyNode`

Wraps a DSPy module so the stargraph execution loop can dispatch it like any other
[`NodeBase`](base.md). Pydantic run-state fields project to DSPy signature
inputs (and outputs project back) via the user-supplied `signature_map`.

## Constructor

`DSPyNode` is constructed via `stargraph.adapters.dspy.bind(...)`, **not directly**
— `bind` installs the force-loud `_LoudFallbackFilter` on the DSPy
`json_adapter` logger before any module call can occur (FR-6).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `module` | `Any` (DSPy module-compatible callable) | required | Wrapped DSPy module. |
| `adapter` | `dspy.JSONAdapter` (force-loud config) | required | Default JSON adapter — `use_native_function_calling=True`. |
| `chat_adapter` | `dspy.ChatAdapter` | required | Chat-style adapter — `use_json_adapter_fallback=False`. |
| `signature_map` | `SignatureMap` \| `Any` | required | Mapping from stargraph state-field names to DSPy signature input/output names. |

All four are keyword-only.

## State contract

- **Reads** — every key listed in `signature_map` (when it is a `dict[str, str]`)
  is read off the run state.
- **Writes** — DSPy `Prediction` attributes mapped back to stargraph state-field
  names. `dict`-shaped results pass through; anything else is wrapped under
  `"output"` so the merge step always receives a dict.

## Side effects + replay

- `side_effects = external` (LLM call).
- `replay_policy = must-stub` — replay is driven by the vcrpy cassette
  (`tests/fixtures/dspy-cassette.yaml`); record mode `none` in CI loud-fails any
  accidental live LLM call.

See [`SideEffects`](../ir-schema.md) and [Engine: replay](../../engine/replay.md).

## YAML

```yaml
ir_version: "1.0.0"
id: "run:triage"
nodes:
  - id: triage
    kind: dspy
    config:
      signature: "user_query -> answer"     # inline DSPy signature (required)
      module: cot                           # predict (default) | cot
      instructions: "Answer concisely."     # optional signature instructions
      # signature_map: {state_field: sig_field}  # default: identity over inputs
      # model: openai/gpt-4o-mini           # optional per-node LM override
      # api_base: http://localhost:41001    # optional, with model
      # api_key_env: MY_LM_KEY              # key read from env, never the IR
state_schema:
  user_query: str
  answer: str
```

The `kind: dspy` factory (`stargraph.nodes.registry`) builds a real `DSPyNode`
from `config` via `stargraph.nodes.dspy.dspy_node_from_config`, which binds
through `stargraph.adapters.dspy.bind(...)` (force-loud adapter). An LM must be
configured — the global one (`stargraph run --lm-url/--lm-model`, or
`dspy.configure(lm=...)`) or a per-node `config.model` override — otherwise the
graph **build** fails loudly instead of failing mid-run. `config: {stub: true}`
is the only route to the deterministic offline stub (cassette fixtures, demos
booting without an LM).

## Errors

!!! warning "Loud fallback"
    On *any* invocation failure — Pydantic constraint violation in the
    structured-output parser, malformed signature map, `TypeError` because the
    wrapped object is not a DSPy module — `acall` emits the canonical DSPy
    fallback warning to `dspy.adapters.json_adapter`. The
    `_LoudFallbackFilter` installed by `stargraph.adapters.dspy.bind` converts
    that warning into [`AdapterFallbackError`][adapter-fallback]. There is **no
    success path through a fallback**.

`AdapterFallbackError` also raises directly if the filter is somehow absent
(caller bypassed `bind`) — the seam never silents.

[adapter-fallback]: ../python/index.md

## See also

- [`NodeBase`](base.md) — abstract contract.
- [`SubGraphNode`](subgraph.md) — composes DSPy nodes inside a child sequence.
