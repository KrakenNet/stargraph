# SPDX-License-Identifier: Apache-2.0
"""MLProgram — the DSPy generator for model nodes, bound to the shared SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *model node* signature (the fields a model-node generation emits) and
``coerce`` (Prediction → plain dict). LM construction + ``clarify`` are re-exported
from the shared core so callers import them from here.
"""

from __future__ import annotations

from typing import Any

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills._smith.lm import (
    DEFAULT_OLLAMA_URL,
    clarify,
    configure_lm,
    make_lm,
)
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict
from stargraph.skills.mlsmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "MLProgram",
    "MLSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class MLSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph ML model node (a ``trainer.py`` module) from a brief.

    A Stargraph model node runs a small classical-ML model in a graph via
    :class:`stargraph.nodes.ml.MLNode`. You do NOT write the node — you write the
    TRAINER that produces the model file the node loads. The gate runs your trainer,
    pins the model's sha256, constructs a real MLNode against it, and runs it on the
    fixture, so the trainer must actually fit + serialize a working model. Honor every
    lesson in ``lessons`` and fix every issue in ``last_findings``.

    ``runtime`` is the loader the node uses; pick exactly one and serialize to match:
    - ``"sklearn"`` — fit a scikit-learn estimator and ``joblib.dump(model, path)``.
    - ``"onnx"`` — fit a scikit-learn estimator, convert with
      ``skl2onnx.convert_sklearn(model, initial_types=[("input",
      FloatTensorType([None, <n_features>]))])`` and write ``onx.SerializeToString()``
      to ``path`` as bytes.

    ``trainer_source`` is saved as ``trainer.py`` and MUST define, at module level:
    - ``def build_model(path: str) -> None`` — fit a model on a SMALL hand-written
      in-code dataset (no file/network reads, no downloads) and serialize it to
      ``path`` per the chosen ``runtime``. Keep the model tiny and deterministic
      (e.g. a ``DecisionTreeClassifier(random_state=0)`` on a handful of rows) so the
      prediction is stable.

    Put every import at module top level. Do NOT import anything you do not use (an
    unused import fails the static gate).

    ``test_source`` is saved as ``test_trainer.py`` BESIDE it. It MUST import from the
    module by bare name (``from trainer import build_model``), call it against a temp
    path, load the result, and assert a prediction with plain ``def test_*()`` +
    ``assert``. Do NOT ``import pytest`` or import anything unused.

    ``input_field`` / ``output_field`` are the state fields the node reads inputs from
    and writes the prediction to (default ``"x"`` / ``"y"``).

    ``fixture`` drives the contract run:
    - ``input``: the value placed on the input field. For ``sklearn`` pass a BATCHED
      2-D list (e.g. ``[[1.0]]``) — it goes straight to ``model.predict``. For
      ``onnx`` pass a single rank-1 feature vector (e.g. ``[1.0]``) — the node adds
      the batch axis.
    - ``expects``: the EXACT prediction the node returns. For ``sklearn`` this is the
      predict array as a list (e.g. ``[1]``); for ``onnx`` a single-sample run is
      unwrapped to a scalar (e.g. ``1``).
    """

    brief: str = dspy.InputField(desc="what the model should predict")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: the MLNode contract + the runtime loaders + accepted examples + web"
    )

    model_name: str = dspy.OutputField(desc="a short kebab-case id for the model")  # pyright: ignore[reportUnknownMemberType]
    runtime: str = dspy.OutputField(desc='the loader: "sklearn" or "onnx"')  # pyright: ignore[reportUnknownMemberType]
    input_field: str = dspy.OutputField(desc='state field to read from (default "x")')  # pyright: ignore[reportUnknownMemberType]
    output_field: str = dspy.OutputField(desc='state field to write to (default "y")')  # pyright: ignore[reportUnknownMemberType]
    trainer_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="trainer.py: build_model(path) that fits + serializes per runtime"
    )
    fixture: dict[str, Any] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="{input: <vector>, expects: <prediction>} — shapes per runtime (see docstring)"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_trainer.py: import via `from trainer import build_model`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "model_name": str(getattr(pred, "model_name", "")),
        "runtime": str(getattr(pred, "runtime", "")),
        "input_field": str(getattr(pred, "input_field", "") or "x"),
        "output_field": str(getattr(pred, "output_field", "") or "y"),
        "trainer_source": str(getattr(pred, "trainer_source", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class MLProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=MLSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
