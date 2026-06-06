# `MLNode`

Graph node that runs a classical-ML model through one of three runtimes:
sklearn, xgboost, or onnx. Wraps the loaders in `stargraph.ml.loaders`.

## Constructor

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `model_id` | `str` | required | Unique identifier within the model registry. |
| `version` | `str` | required | Semver-ish version; forms the cache key with `model_id` for the ONNX session pool. |
| `runtime` | `Literal["sklearn", "xgboost", "onnx"]` | required | Runtime selector. |
| `file_uri` | `str \| None` | `None` | `file://` URI of the model bytes. `None` defers to a registry lookup at execute time (Phase-3 stub — registry lands in task 3.38). |
| `allow_unsafe_pickle` | `bool` | `False` | Default-deny gate for the sklearn unsafe-deserialize path (FR-30 antipattern guard #4). No effect on xgboost / onnx. |
| `expected_sha256` | `str \| None` | `None` | Optional pinned SHA-256 of the model file; verified before any deserialize step. |
| `input_field` | `str` | `"x"` | State field to read inference inputs from. |
| `output_field` | `str` | `"y"` | State field to write predictions to. |

All parameters are keyword-only.

!!! warning "Eager construction"
    Construction is eager-validated: the runtime is checked, the safe-deserialize
    gate fires for `runtime="sklearn"` when `allow_unsafe_pickle=False`, and the
    underlying ONNX session is warmed via the module-scope cache. There is no
    path where `allow_unsafe_pickle=False` plus a sklearn `file://` URI builds
    a usable node — the failure mode is identical whether the graph is built
    up-front or lazily.

## State contract

- **Reads** — `state.<input_field>` (default `state.x`).
- **Writes** — `{output_field: predictions}` (default `{"y": predictions}`).

Inference is offloaded to a worker thread via `asyncio.to_thread` so the event
loop is never blocked by a sync `.predict(...)` call.

## Side effects + replay

- `side_effects = none` — inference is a pure function of the loaded model and
  the input.
- Replay re-executes natively unless the registered `content_hash` changed
  (registry mismatch raises `IncompatibleModelHashError`, which the FR-21
  `must-stub` envelope routes through the recorded cassette).

## YAML

```yaml
nodes:
  - id: infer_node
    kind: ml
    spec:
      model_id: "$state.model_id"
      version: "$state.version"
      runtime: sklearn
      input_field: x
      output_field: y
      allow_unsafe_pickle: true
```

See `tests/fixtures/training-subgraph.yaml` for the full training-as-subgraph
recipe (FR-32, design §3.9.4).

## Errors

All errors are [`MLNodeError`](../python/index.md):

- Unsupported runtime (not one of `sklearn` / `xgboost` / `onnx`).
- `runtime="sklearn"` with `allow_unsafe_pickle=False` — message includes
  `set allow_unsafe_pickle=True to opt in`.
- `runtime="onnx"` reaching `_predict` with no warmed session
  (`onnx session not initialised`).
- Other runtimes reaching `_predict` with no loaded model (`model not loaded`).
- Loader errors (sidecar skew, hash mismatch, `.bin` xgboost) propagate from
  `stargraph.ml.loaders` at construction time.

## See also

- [`NodeBase`](base.md) — abstract contract.
- [`SubGraphNode`](subgraph.md) — composes train + register + infer per
  design §3.9.4.
