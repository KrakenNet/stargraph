# SPDX-License-Identifier: Apache-2.0
"""Train the SOC-triage severity classifier, export to ONNX, print its SHA-256.

The model scores a synthetic SOC-alert feature vector into a 3-class risk
band (``0=low``, ``1=medium``, ``2=high``). It is consumed by the
soc-triage++ graph's :class:`harbor.nodes.ml.MLNode` (``runtime="onnx"``)
with the printed SHA-256 pinned into ``harbor.yaml`` (task 1.29) so the
:class:`harbor.ml.registry.ModelRegistry` verifies the content hash on
every load.

Feature schema (7 float32 columns, order is the contract for the graph)::

    [ severity_raw,           # 0..10  raw SIEM severity
      asset_tier_dev,         # one-hot asset tier (dev / staging / prod)
      asset_tier_staging,
      asset_tier_prod,
      source_reputation,      # 0..1   higher = more trustworthy source
      hour_of_day,            # 0..23
      repeat_count ]          # how often this alert has re-fired

ONNX tensor interface (what MLNode reads):

* single input tensor ``float_input`` -- ``tensor(float)`` shape ``[None, 7]``
  (skl2onnx default name; ``MLNode`` reads ``get_inputs()[0].name`` so the
  name is discovered dynamically, but it is pinned here for clarity).
* ``output[0]`` -- predicted label ``tensor(int64)`` shape ``[None]``.
* ``output[1]`` -- class probabilities ``tensor(float)`` shape ``[None, 3]``
  (``zipmap=False`` keeps this a plain float tensor, not a list-of-dicts).

``MLNode._predict`` returns ``session.run(None, {input_name: x})[0]`` -- i.e.
the label tensor -- so the graph's ``output_field`` receives the risk class;
the probability tensor is available as ``output[1]`` for the confidence band.

Usage::

    uv run python demos/soc-triage/scripts/train_severity.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from skl2onnx import to_onnx
from skl2onnx.common.data_types import FloatTensorType
from sklearn.ensemble import RandomForestClassifier

# Deterministic seed so the trained model (and demo predictions) are
# reproducible run to run. The exported .onnx is not byte-stable across
# skl2onnx versions, so the SHA-256 is printed on every run rather than
# asserted here -- task 1.29 pins whatever this run produces.
_SEED = 1337
_N_SAMPLES = 4000
_N_FEATURES = 7
_INPUT_NAME = "float_input"

# Risk bands.
_LOW, _MEDIUM, _HIGH = 0, 1, 2

_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "severity_classifier.onnx"


def _synthesize(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Generate synthetic SOC alerts with plausible risk semantics.

    The label is derived from a hand-tuned risk score so the classifier
    learns a sensible decision surface: high raw severity, a production
    asset, a low-reputation source, and a high repeat count all push an
    alert toward ``high``; the inverse pushes toward ``low``.
    """
    severity_raw = rng.uniform(0.0, 10.0, _N_SAMPLES)
    tier = rng.integers(0, 3, _N_SAMPLES)  # 0=dev, 1=staging, 2=prod
    tier_onehot = np.eye(3, dtype=np.float64)[tier]
    source_reputation = rng.uniform(0.0, 1.0, _N_SAMPLES)
    hour_of_day = rng.uniform(0.0, 24.0, _N_SAMPLES)
    repeat_count = rng.integers(0, 10, _N_SAMPLES).astype(np.float64)

    features = np.column_stack(
        [
            severity_raw,
            tier_onehot,
            source_reputation,
            hour_of_day,
            repeat_count,
        ]
    ).astype(np.float32)

    # Continuous risk score in [0, 1]-ish range, then bucketed into bands.
    # Production tier (tier==2) is the dominant escalator; low reputation
    # and repeat firings stack on top of raw severity.
    tier_weight = np.choose(tier, [0.0, 0.15, 0.35])
    score = (
        0.45 * (severity_raw / 10.0)
        + tier_weight
        + 0.25 * (1.0 - source_reputation)
        + 0.15 * (repeat_count / 9.0)
    )
    # A little night-shift bump (alerts at 0-5h are slightly riskier).
    score += np.where(hour_of_day < 5.0, 0.05, 0.0)
    score += rng.normal(0.0, 0.04, _N_SAMPLES)  # label noise so trees generalize

    labels = np.full(_N_SAMPLES, _MEDIUM, dtype=np.int64)
    labels[score < 0.40] = _LOW
    labels[score >= 0.70] = _HIGH
    return features, labels


def _sha256(path: Path) -> str:
    """Stream-hash ``path`` with SHA-256 and return the hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    rng = np.random.default_rng(_SEED)
    features, labels = _synthesize(rng)

    clf = RandomForestClassifier(
        n_estimators=64,
        max_depth=8,
        random_state=_SEED,
        n_jobs=1,  # single-threaded → deterministic tree construction
    )
    clf.fit(features, labels)

    onnx_model = to_onnx(
        clf,
        initial_types=[(_INPUT_NAME, FloatTensorType([None, _N_FEATURES]))],
        target_opset=17,
        options={id(clf): {"zipmap": False}},
    )

    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MODEL_PATH.write_bytes(onnx_model.SerializeToString())

    digest = _sha256(_MODEL_PATH)
    print(f"wrote {_MODEL_PATH}")
    print(f"train accuracy: {clf.score(features, labels):.4f}")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
