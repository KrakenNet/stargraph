# SPDX-License-Identifier: Apache-2.0
"""Happy-path integration tests for :class:`MLNode` with the xgboost runtime (FR-30).

Loads the bundled ``tests/fixtures/xgboost_minimal.ubj`` Booster
(saved as Universal Binary JSON, the default since xgboost 2.1) and
verifies the ``.bin`` rejection path raises
:class:`stargraph.errors.MLNodeError` *before* any xgboost C-extension
call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

xgb = pytest.importorskip("xgboost")

from stargraph.errors import MLNodeError  # noqa: E402  (importorskip guard above)
from stargraph.ml.loaders import load_xgboost_model  # noqa: E402
from stargraph.nodes.ml import MLNode  # noqa: E402

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_XGB_MODEL = _FIXTURES / "xgboost_minimal.ubj"


def test_load_xgboost_ubj_happy_path() -> None:
    booster = load_xgboost_model(
        model_id="xgb-bin",
        version="v1",
        file_uri=_XGB_MODEL.as_uri(),
    )
    assert isinstance(booster, xgb.Booster)
    X = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)  # noqa: N806 -- xgboost convention
    preds = booster.predict(xgb.DMatrix(X))
    assert preds.shape == (2,)


def test_load_xgboost_rejects_bin_suffix(tmp_path: Path) -> None:
    """``.bin`` is the legacy format removed in xgboost 3.1; reject loud."""
    bin_path = tmp_path / "model.bin"
    bin_path.write_bytes(b"not-actually-xgboost")
    with pytest.raises(MLNodeError, match=r"\.ubj or \.json"):
        load_xgboost_model(
            model_id="xgb-bin",
            version="v1",
            file_uri=bin_path.as_uri(),
        )


def test_mlnode_xgboost_execute_dispatches_to_predict() -> None:
    """MLNode wraps the Booster and routes .execute -> Booster.predict.

    The Booster's ``.predict`` accepts a DMatrix directly, so the
    state field hands one in.
    """
    node = MLNode(
        model_id="xgb-bin",
        version="v1",
        runtime="xgboost",
        file_uri=_XGB_MODEL.as_uri(),
        input_field="x",
        output_field="y",
    )

    X = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)  # noqa: N806 -- xgboost convention

    class _State:
        x = xgb.DMatrix(X)

    class _Ctx:
        run_id = "test-run"

    out = asyncio.run(node.execute(_State(), _Ctx()))  # type: ignore[arg-type]
    assert "y" in out
    assert out["y"].shape == (2,)
