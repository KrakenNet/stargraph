# `WriteArtifactNode`

Built-in node (FR-92, design §10.3) that persists a state-resident byte payload
through a configured `ArtifactStore`, emits an `ArtifactWrittenEvent`, and
patches the resulting `ArtifactRef` into state.

## `WriteArtifactNodeConfig`

Pydantic config (subclasses `IRBase`, so `extra="forbid"` — unknown keys fail
loudly at validation time, FR-6, AC-9.1).

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `content_field` | `str` | required | State attribute holding the artifact payload (`bytes` or `str`). |
| `name` | `str` | required | Logical filename hint persisted in `ArtifactRef.name`. |
| `content_type` | `str` | required | MIME type persisted in sidecar metadata + `ArtifactRef.content_type`. |
| `metadata` | `dict[str, Any]` | `{}` | Extra free-form metadata merged into the sidecar (under `content_type`). |
| `output_field` | `str` | `"artifact_ref"` | State key receiving the resulting `ArtifactRef` (JSON-mode `model_dump`). |
| `replay_policy` | `Literal["must_stub", "fail_loud"]` | `"must_stub"` | Replay routing per design §10.3. |

## Constructor

```python
WriteArtifactNode(*, config: WriteArtifactNodeConfig)
```

Single keyword-only arg; the config is attached at construction time.

## State contract

- **Reads** — `state.<content_field>`. Coerces `str` → UTF-8 bytes; passes
  `bytes` through; accepts `bytearray` and `memoryview` (converted via
  `bytes(...)`). Anything else raises `TypeError`.
- **Writes** — `{output_field: ref.model_dump(mode="json")}` (default
  `{"artifact_ref": {...}}`).

## Required context — `WriteArtifactContext`

`ctx` must satisfy this `runtime_checkable` Protocol (Phase-1
[`ExecutionContext`](base.md) only pins `run_id`):

| Field | Type | Notes |
| --- | --- | --- |
| `run_id` | `str` | |
| `step` | `int` | Stamped on every event + on the persisted `ArtifactRef` provenance. |
| `bus` | `Any` | Must expose `async send(event, *, fathom=...)`. |
| `artifact_store` | `ArtifactStore` | Resolved provider for this run. |
| `is_replay` | `bool` | Honoured by `replay_policy`. |
| `fathom` | `Any` | Optional `FathomAdapter` for transition mirroring. |

## Side effects + replay

- `side_effects = SideEffects.write`.
- `replay_policy="must_stub"` (default) — node does **not** call
  `ArtifactStore.put` when `ctx.is_replay` is `True`; the upstream cassette
  layer is expected to surface a recorded `ArtifactRef` ahead of dispatch.
- `replay_policy="fail_loud"` — node raises immediately on any replay-time
  call, surfacing wiring bugs without ambiguity.

!!! note "Cassette wiring still pending"
    Today the runtime cassette layer for nodes is not yet wired
    (`harbor.runtime.tool_exec` covers tool calls only). Reaching either
    replay branch with the current runtime means the cassette wiring is
    incomplete; both raise `ArtifactStoreError` rather than re-writing.

## Event

Emits one `ArtifactWrittenEvent` carrying:

```python
provenance = {
    "origin": "tool",
    "source": "harbor.artifacts",
    "run_id": ctx.run_id,
    "step": ctx.step,
    "confidence": 1.0,
    "timestamp": "<iso>",
}
```

## YAML

```yaml
nodes:
  - id: write_record
    kind: write_artifact
state_schema:
  record_bytes: bytes
```

See `tests/fixtures/triage_no_nautilus.yaml` and
`tests/fixtures/triage_stub_broker.yaml` for full pipeline shapes.

## Errors

- `AttributeError` — `ctx` does not satisfy `WriteArtifactContext` (missing
  `run_id`, `step`, `bus`, `artifact_store`, `is_replay`, or `fathom`).
- `TypeError` — `state.<content_field>` is not `bytes` / `str` /
  `bytearray` / `memoryview`. Silent coercion of arbitrary objects would mask
  wiring bugs (FR-6).
- `ArtifactStoreError` (`reason="replay-fail-loud"`) — `is_replay=True`
  with `replay_policy="fail_loud"`.
- `ArtifactStoreError` (`reason="replay-stub-missing"`) — `is_replay=True`
  with `replay_policy="must_stub"` (cassette layer not yet wired).

## See also

- [`NodeBase`](base.md) — abstract contract.
- [`InterruptNode`](interrupt.md) — same construction convention.
- [Engine: replay](../../engine/replay.md) — cassette layer.
