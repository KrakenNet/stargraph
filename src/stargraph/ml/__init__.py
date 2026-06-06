# SPDX-License-Identifier: Apache-2.0
"""stargraph.ml -- ML model loaders + (eventual) tiny SQLite registry (FR-30, FR-31).

Phase 3 ships :mod:`stargraph.ml.loaders` with the three runtime-specific
loader functions (sklearn / xgboost / onnx) used by
:class:`stargraph.nodes.ml.MLNode`. The SQLite tiny-model registry
(design §3.9.3) lands separately in task 3.38.
"""

from __future__ import annotations

from stargraph.ml.loaders import (
    get_onnx_session,
    load_sklearn_model,
    load_xgboost_model,
)

__all__ = [
    "get_onnx_session",
    "load_sklearn_model",
    "load_xgboost_model",
]
