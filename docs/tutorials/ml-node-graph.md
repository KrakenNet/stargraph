# Tutorial: Classical ML in a Graph

In this tutorial you'll drop an `MLNode` into a graph that scores an
input feature vector through a classical-ML model. ONNX is the
preferred path: deterministic, no unsafe-load surface, cached one
session per `(model_id, version)`. Sklearn is supported with a
default-deny safe-load gate; we'll show how to opt in correctly.

## What you'll build

```mermaid
flowchart LR
    start((start)) --> score[node_score — MLNode (ONNX)]
    score --> halt[node_halt — halt]
```

State carries `x: list[list[float]]` (the feature batch) and
`y: list[int]` (the predicted class indices). `MLNode` is async-aware:
it offloads `predict`/`run` to a worker thread via `asyncio.to_thread`
so the event loop is never blocked.

## Prerequisites

- `pip install 'stargraph[ml]'` — adds `onnxruntime`, `joblib`,
  `scikit-learn`, `xgboost` extras.
- A scratch directory with an ONNX model file. The
  `tests/fixtures/onnx_minimal.onnx` shipped with Stargraph works, or
  export your own with `skl2onnx`.
- Working knowledge of the [first graph](first-graph.md) tutorial.

## Step 1 — Stage the model

Copy the fixture model into the project (or export your own to the
same path):

```bash
mkdir -p models
cp /path/to/stargraph/tests/fixtures/onnx_minimal.onnx models/clf.onnx
```

`MLNode` resolves the model bytes through a `file://` URI;
remote schemes (`s3://`, `gs://`) are deferred per FR-30.

## Step 2 — Define the state model

```python
# state.py
from __future__ import annotations

from pydantic import BaseModel, Field


class ScoringState(BaseModel):
    x: list[list[float]] = Field(default_factory=list)
    y: list[int] = Field(default_factory=list)
```

## Step 3 — Wire the MLNode (ONNX path)

Save this as `score.py`. `MLNode.__init__` is eager — the ONNX
session is opened (and cached at module scope) at construction time,
so any sidecar skew, schema mismatch, or missing file fails loud
before the engine even reaches `execute`.

```python
# score.py
from __future__ import annotations

from stargraph.nodes.ml import MLNode


class ScoreNode(MLNode):
    """Zero-arg `MLNode` subclass so the IR's `kind:` resolver can
    instantiate it directly. Constants pin the model identity tuple
    `(model_id, version)` used by the ONNX session cache.
    """

    def __init__(self) -> None:
        super().__init__(
            model_id="clf",
            version="1.0.0",
            runtime="onnx",
            file_uri="file://models/clf.onnx",
            # Optional: pin the file's sha256 so any drift fails loud.
            # expected_sha256="...",
            input_field="x",
            output_field="y",
        )
```

The provider list is fixed to `["CPUExecutionProvider"]` inside the
loader to defeat onnxruntime's silent CPU→GPU fallback (issue #25145).
Effective providers are logged at INFO on first session create.

## Step 4 — Author the graph

```yaml
# graph.yaml
ir_version: "1.0.0"
id: "run:ml-hello"
state_class: "state:ScoringState"
nodes:
  - id: node_score
    kind: "score:ScoreNode"
  - id: node_halt
    kind: halt
rules:
  - id: r-score-to-halt
    when: "?n <- (node-id (id node_score))"
    then:
      - kind: goto
        target: node_halt
  - id: r-halt
    when: "?n <- (node-id (id node_halt))"
    then:
      - kind: halt
        reason: "scored"
```

## Step 5 — Run it

`--inputs` forwards JSON-typed values per the IR's state schema:

```bash
uv run stargraph run graph.yaml \
  --inputs 'x=[[1.0, 2.0, 3.0, 4.0]]' \
  --log-file ./.stargraph/audit.jsonl
```

Expected last line:

```
run_id=run-… status=done
```

Inspect the predicted class:

```bash
uv run stargraph inspect "$RUN_ID" --db ./.stargraph/run.sqlite --step 1
```

The state JSON will include `"y": [<class_index>]`.

## The sklearn safe-load gate

Sklearn estimators are typically distributed as joblib-packed binary
files. Stargraph refuses to load one by default — `MLNode.__init__`
raises `MLNodeError("pickle disabled; set allow_unsafe_pickle=True
to opt in")` *before* opening the file. Two reasons it stays
default-deny:

1. The serialisation format is arbitrary code at deserialise time.
2. `__sklearn_version__` skew between writer and reader silently
   degrades estimator behaviour; the loader pairs the gate with a
   sidecar version check (`<model>.pkl.sklearn_version`) so any skew
   raises `IncompatibleSklearnVersion` rather than running.

To opt in safely, pin the file's sha256 and set the override:

```python
MLNode(
    model_id="clf",
    version="1.0.0",
    runtime="sklearn",
    file_uri="file://models/clf.pkl",
    allow_unsafe_pickle=True,
    expected_sha256="abc123...",   # required for production
)
```

The order of operations inside `loaders.load_sklearn_model` is:

1. Default-deny gate — `MLNodeError` if `allow_unsafe_pickle=False`.
2. SHA-256 verification — `IncompatibleModelHashError` on mismatch.
3. Sidecar `__sklearn_version__` check —
   `IncompatibleSklearnVersion` if the writer's sklearn version
   differs from the runtime's.
4. `joblib.load` with `InconsistentVersionWarning → error` so
   anything that slips past the sidecar still fails loud.

Prefer ONNX in production; reserve sklearn for trusted internal
artifacts that you SHA-pin.

## XGBoost path

```python
MLNode(
    model_id="boost",
    version="1.0.0",
    runtime="xgboost",
    file_uri="file://models/clf.ubj",   # or .json
)
```

The legacy binary `.bin` format is rejected outright (removed in
xgboost 3.1). Use `Booster.save_model("clf.ubj")` when exporting.

## Troubleshooting

- **`MLNodeError: pickle disabled…`** — you're on the sklearn path
  without the explicit opt-in. Either switch to ONNX, or pin sha256
  + set `allow_unsafe_pickle=True`.
- **`MLNodeError: unsupported runtime…`** — only `"sklearn"`,
  `"xgboost"`, `"onnx"` are accepted.
- **`IncompatibleModelHashError`** — the model bytes don't match
  `expected_sha256`. Re-hash the file with `sha256sum` and update
  the constant.

## What to read next

- [Reference → nodes / ml](../reference/nodes/ml.md) — full
  `MLNode` constructor surface and registry hooks.
- [Engine → replay](../engine/replay.md) — how `MLNode` outputs
  participate in cassette determinism (sklearn predictions are
  deterministic given fixed input; ONNX graphs use a single CPU EP
  to keep this true).
- `src/stargraph/ml/loaders.py` — read the per-runtime gates if you're
  porting a non-trivial model.
