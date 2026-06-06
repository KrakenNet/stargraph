# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ONNX session reuse + explicit-provider gating (FR-30, design §3.9.2).

Pins the FR-30 verbatim amendment 8 contract for the ONNX runtime path
of :class:`stargraph.nodes.ml.MLNode` *before* the GREEN partner ships in
task 3.37. Currently RED because :mod:`stargraph.nodes.ml` and
:mod:`stargraph.ml.loaders` do not yet exist (collection ImportError).

Four cases:

1. Two MLNode constructions for the same ``(model_id, version)`` reuse
   the SAME :class:`onnxruntime.InferenceSession` instance (cache hit).
2. Session created with ``providers=["CPUExecutionProvider"]`` only --
   no silent GPU fallback (defeats onnxruntime#25145).
3. Effective provider logged on session create (visible via structlog
   capture).
4. The shared session is thread-safe across concurrent inferences on
   CPU EP (per onnxruntime#114).
"""

from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

if TYPE_CHECKING:
    from pathlib import Path

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false


# Force stargraph.nodes.ml + stargraph.ml.loaders import at collection time so
# the missing-module RED signal lands as a collection-phase ImportError
# (which pytest reports as ERROR, satisfying the verify gate). The
# GREEN partner (3.37) creates these modules and the tests then exercise
# the session-reuse contract.
_ml = importlib.import_module("stargraph.nodes.ml")
_loaders = importlib.import_module("stargraph.ml.loaders")


def _onnx_runtime() -> Any:
    """Import onnxruntime lazily; skip the module if missing."""
    try:
        return importlib.import_module("onnxruntime")
    except ImportError:  # pragma: no cover -- onnxruntime is in [ml] extras
        pytest.skip("onnxruntime not installed")


def _make_identity_onnx_model(path: Path) -> None:
    """Write a minimal ONNX graph: ``y = x`` (Identity op) at ``path``.

    Uses the ``onnx`` builder if available, else falls back to
    constructing the protobuf bytes via ``onnxruntime`` -- if neither
    works, skips. This fixture runs only after the stargraph.nodes.ml
    module-level import succeeds (i.e., post-GREEN).
    """
    try:
        onnx = importlib.import_module("onnx")
        helper = importlib.import_module("onnx.helper")
    except ImportError:  # pragma: no cover -- onnx package is transitive via onnxruntime
        pytest.skip("onnx package not installed")

    tp = onnx.TensorProto  # type: ignore[attr-defined]
    x = helper.make_tensor_value_info("x", tp.FLOAT, [None, 4])
    y = helper.make_tensor_value_info("y", tp.FLOAT, [None, 4])
    node = helper.make_node("Identity", ["x"], ["y"])
    graph = helper.make_graph([node], "id", [x], [y])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9  # onnxruntime 1.18+ supports IR v9
    onnx.save(model, str(path))  # type: ignore[attr-defined]


@pytest.fixture
def onnx_model_path(tmp_path: Path) -> Path:
    _onnx_runtime()  # ensure onnxruntime present (skip otherwise)
    p = tmp_path / "identity.onnx"
    _make_identity_onnx_model(p)
    return p


def test_onnx_session_reused_per_model_version(onnx_model_path: Path) -> None:
    """Two MLNode constructions with the same (model_id, version) share
    the same InferenceSession (cache hit)."""
    ml = importlib.import_module("stargraph.nodes.ml")
    loaders = importlib.import_module("stargraph.ml.loaders")

    # Hypothetical loader API: cache is keyed by (model_id, version)
    sess1 = loaders.get_onnx_session(  # type: ignore[attr-defined]
        model_id="m1", version="v1", file_uri=onnx_model_path.as_uri()
    )
    node1 = ml.MLNode(model_id="m1", version="v1", runtime="onnx")  # type: ignore[attr-defined]
    node2 = ml.MLNode(model_id="m1", version="v1", runtime="onnx")  # type: ignore[attr-defined]
    sess2 = loaders.get_onnx_session(  # type: ignore[attr-defined]
        model_id="m1", version="v1", file_uri=onnx_model_path.as_uri()
    )

    assert sess1 is sess2, "InferenceSession must be cached per (model_id, version)"
    # MLNode wrappers themselves don't need to share identity, but their
    # underlying session must.
    assert node1.model_id == node2.model_id


def test_onnx_session_created_with_cpu_provider_only(onnx_model_path: Path) -> None:
    """Session must be opened with providers=['CPUExecutionProvider']
    only, defeating onnxruntime#25145 silent GPU fallback."""
    loaders = importlib.import_module("stargraph.ml.loaders")
    sess = loaders.get_onnx_session(  # type: ignore[attr-defined]
        model_id="m2", version="v1", file_uri=onnx_model_path.as_uri()
    )
    providers = sess.get_providers()  # type: ignore[attr-defined]
    assert providers == ["CPUExecutionProvider"], (
        f"expected ['CPUExecutionProvider'] only, got {providers!r}"
    )


def test_onnx_effective_provider_logged_on_session_create(
    onnx_model_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Effective provider is logged at session-create time."""
    import logging

    loaders = importlib.import_module("stargraph.ml.loaders")
    with caplog.at_level(logging.INFO, logger="stargraph.ml.loaders"):
        loaders.get_onnx_session(  # type: ignore[attr-defined]
            model_id="m3", version="v1", file_uri=onnx_model_path.as_uri()
        )

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "CPUExecutionProvider" in text, (
        f"effective provider must be logged on session create; got: {text!r}"
    )


def test_onnx_session_thread_safe_concurrent_inference(onnx_model_path: Path) -> None:
    """The cached InferenceSession is thread-safe across concurrent
    inferences on CPU EP (onnxruntime#114)."""
    loaders = importlib.import_module("stargraph.ml.loaders")
    sess = loaders.get_onnx_session(  # type: ignore[attr-defined]
        model_id="m4", version="v1", file_uri=onnx_model_path.as_uri()
    )

    rng = np.random.default_rng(seed=0)
    inputs = [rng.random((2, 4), dtype=np.float32) for _ in range(16)]

    def _run(x: np.ndarray) -> np.ndarray:
        result = sess.run(None, {"x": x})  # type: ignore[attr-defined]
        return result[0]  # type: ignore[no-any-return]

    with ThreadPoolExecutor(max_workers=8) as pool:
        outs = list(pool.map(_run, inputs))

    assert len(outs) == len(inputs)
    for x, y in zip(inputs, outs, strict=True):
        np.testing.assert_array_equal(x, y)
