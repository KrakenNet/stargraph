# SPDX-License-Identifier: Apache-2.0
"""Happy-path integration tests for :class:`MLNode` with the sklearn runtime (FR-30).

The pickle gate + version-skew RED tests live in
``tests/integration/test_ml_pickle_safety.py``; this file exercises the
opt-in ``allow_unsafe_pickle=True`` happy path against the
``tests/fixtures/sklearn_minimal.joblib`` LogisticRegression fixture
shipped under the spec.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sklearn

from stargraph.errors import IncompatibleModelHashError
from stargraph.ml.loaders import load_sklearn_model
from stargraph.nodes.ml import MLNode

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_SKLEARN_MODEL = _FIXTURES / "sklearn_minimal.joblib"


@pytest.fixture
def sklearn_model_uri(tmp_path: Path) -> str:
    """Copy the bundled sklearn fixture next to a matching sidecar.

    The fixture is regenerated under the current sklearn version when
    needed; the sidecar pins the same version so the version-skew gate
    inside :func:`load_sklearn_model` is a no-op for the happy path.
    """
    target = tmp_path / "model.joblib"
    target.write_bytes(_SKLEARN_MODEL.read_bytes())
    sidecar = target.with_suffix(target.suffix + ".sklearn_version")
    sidecar.write_text(str(sklearn.__version__), encoding="utf-8")
    return target.as_uri()


def test_load_sklearn_happy_path_with_opt_in(sklearn_model_uri: str) -> None:
    """``allow_unsafe_pickle=True`` returns a usable estimator."""
    model: Any = load_sklearn_model(
        model_id="logreg",
        version="v1",
        file_uri=sklearn_model_uri,
        allow_unsafe_pickle=True,
    )
    # LogisticRegression has predict; assert it's usable on toy input.
    X = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float64)  # noqa: N806 -- sklearn convention
    y = model.predict(X)
    assert y.shape == (2,)


def test_mlnode_sklearn_execute_dispatches_to_predict(
    sklearn_model_uri: str,
) -> None:
    """MLNode wraps the estimator and dispatches via .execute -> .predict."""
    node = MLNode(
        model_id="logreg",
        version="v1",
        runtime="sklearn",
        file_uri=sklearn_model_uri,
        allow_unsafe_pickle=True,
        input_field="x",
        output_field="y",
    )

    class _State:
        x = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float64)

    class _Ctx:
        run_id = "test-run"

    out = asyncio.run(node.execute(_State(), _Ctx()))  # type: ignore[arg-type]
    assert "y" in out
    assert out["y"].shape == (2,)


def test_mlnode_sklearn_sha256_mismatch_raises(sklearn_model_uri: str) -> None:
    """Pinned SHA-256 mismatch surfaces as IncompatibleModelHashError."""
    with pytest.raises(IncompatibleModelHashError):
        load_sklearn_model(
            model_id="logreg",
            version="v1",
            file_uri=sklearn_model_uri,
            allow_unsafe_pickle=True,
            expected_sha256="0" * 64,  # deliberately wrong digest
        )
