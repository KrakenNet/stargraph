# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed trainers — the trainset cold start.

Each entry is a verified ``(brief → trainer)`` pair: a ``trainer.py`` defining
``build_model(path)`` that fits a tiny deterministic model on an in-code dataset and
serializes it, its ``test_trainer.py``, and the ``fixture`` the contract tier drives a
live MLNode against. One seed per shipped runtime:

- Seed 1 (``sklearn``) fits a ``DecisionTreeClassifier`` and ``joblib.dump``\\s it; the
  node loads it under the opt-in pickle gate.
- Seed 2 (``onnx``) fits the same classifier and exports it via ``skl2onnx``; the node
  loads an ONNX session.

Both produce a model the gate can load (sha-pinned) and predict from — so the contract
tier only passes if the model actually trains, serializes, loads, and predicts. ``id``
is a fixed literal so ``seed_trainset`` is idempotent.

``tests/integration/mlsmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any

# --- Seed 1: sklearn threshold classifier (joblib) --------------------------- #
_SKLEARN_TRAINER = '''\
from __future__ import annotations

import joblib
from sklearn.tree import DecisionTreeClassifier


def build_model(path: str) -> None:
    """Fit a tiny threshold classifier and serialize it with joblib."""
    features = [[0.0], [0.0], [1.0], [1.0]]
    labels = [0, 0, 1, 1]
    model = DecisionTreeClassifier(random_state=0).fit(features, labels)
    joblib.dump(model, path)
'''

_SKLEARN_TEST = """\
import tempfile
from pathlib import Path

import joblib

from trainer import build_model


def test_build_model_writes_a_loadable_classifier() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "model.pkl")
        build_model(path)
        model = joblib.load(path)
        assert model.predict([[1.0]]).tolist() == [1]
        assert model.predict([[0.0]]).tolist() == [0]
"""

_SKLEARN_FIXTURE: dict[str, Any] = {"input": [[1.0]], "expects": [1]}

# --- Seed 2: onnx threshold classifier (skl2onnx export) --------------------- #
_ONNX_TRAINER = '''\
from __future__ import annotations

from pathlib import Path

from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
from sklearn.tree import DecisionTreeClassifier


def build_model(path: str) -> None:
    """Fit a tiny threshold classifier and export it to ONNX."""
    features = [[0.0], [0.0], [1.0], [1.0]]
    labels = [0, 0, 1, 1]
    model = DecisionTreeClassifier(random_state=0).fit(features, labels)
    onx = convert_sklearn(model, initial_types=[("input", FloatTensorType([None, 1]))])
    Path(path).write_bytes(onx.SerializeToString())
'''

_ONNX_TEST = """\
import tempfile
from pathlib import Path

import numpy as np
import onnxruntime as ort

from trainer import build_model


def test_build_model_exports_a_runnable_onnx_model() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "model.onnx")
        build_model(path)
        session = ort.InferenceSession(path)
        name = session.get_inputs()[0].name
        out = session.run(None, {name: np.asarray([[1.0]], dtype=np.float32)})[0]
        assert out.tolist() == [1]
"""

_ONNX_FIXTURE: dict[str, Any] = {"input": [1.0], "expects": 1}


def _pair(
    seed_id: str,
    brief: str,
    model_name: str,
    runtime: str,
    trainer_source: str,
    test_source: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "model_name": model_name,
        "runtime": runtime,
        "input_field": "x",
        "output_field": "y",
        "trainer_source": trainer_source,
        "test_source": test_source,
        "fixture": fixture,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "c0010000001",
        "a model that predicts a binary label from a single numeric feature by threshold (sklearn)",
        "threshold-classifier-sklearn",
        "sklearn",
        _SKLEARN_TRAINER,
        _SKLEARN_TEST,
        _SKLEARN_FIXTURE,
    ),
    _pair(
        "c0010000002",
        "a model that predicts a binary label from a single numeric feature by threshold, as ONNX",
        "threshold-classifier-onnx",
        "onnx",
        _ONNX_TRAINER,
        _ONNX_TEST,
        _ONNX_FIXTURE,
    ),
]
