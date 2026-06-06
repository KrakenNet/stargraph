# SPDX-License-Identifier: Apache-2.0
"""FR-32 reference example: training-as-subgraph (design §3.9.4).

Demonstrates the engine-native "training is just a sub-graph + tool primitives"
recipe -- there is no dedicated training node type. The reference IR lives at
``tests/fixtures/training-subgraph.yaml``; this test executes the logical
steps in Python (the YAML -> graph-runtime path for ML nodes lands later) so
the FR-32 acceptance criterion -- "produces a registered model" -- is
verifiable today against the primitives that already exist:

* :func:`stargraph.tools.decorator.tool` for the ``ml.fit`` / ``ml.register``
  tool seam (side_effects + replay_policy declared inline).
* :class:`stargraph.ml.registry.ModelRegistry` for the SQLite tiny registry
  (FR-31, task 3.38).
* :class:`stargraph.nodes.ml.MLNode` for the inference node (FR-30, task 3.37)
  declared ``side_effects=none`` so replay re-executes natively unless the
  registered ``content_hash`` changes -- in which case
  :class:`stargraph.errors.IncompatibleModelHashError` fires and the FR-21
  ``must-stub`` envelope must route through the recorded cassette.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest
import sklearn
import yaml
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression

from stargraph.errors import IncompatibleModelHashError
from stargraph.ml.registry import ModelRegistry
from stargraph.nodes.ml import MLNode
from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects

# pyright: reportFunctionMemberAccess=false, reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownLambdaType=false

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_REFERENCE_YAML = _FIXTURES / "training-subgraph.yaml"


# --------------------------------------------------------------------------- #
# Sync filesystem helpers (kept sync so async tests don't trip ASYNC240)      #
# --------------------------------------------------------------------------- #


def _write_sklearn_sidecar(artifact_path: str) -> None:
    """Pin the current sklearn version next to the artifact (version-skew gate)."""
    Path(artifact_path + ".sklearn_version").write_text(str(sklearn.__version__), encoding="utf-8")


def _overwrite_artifact(path: Path, payload: bytes) -> None:
    """Simulate a model-version drift by rewriting the artifact bytes in place."""
    path.write_bytes(payload)


def _assert_artifact_exists(artifact_path: str) -> None:
    """Sync helper -- keep the existence check out of the async test body."""
    assert Path(artifact_path).exists(), f"expected artifact at {artifact_path}"


# --------------------------------------------------------------------------- #
# @tool primitives -- the sub-graph leaves                                    #
# --------------------------------------------------------------------------- #


@tool(
    name="fit",
    namespace="ml",
    version="1.0.0",
    side_effects=SideEffects.write,  # writes the joblib artifact
    replay_policy=ReplayPolicy.must_stub,  # never re-train on replay
)
def ml_fit(
    *,
    artifact_path: str,
    n_samples: int = 64,
    n_features: int = 4,
    random_state: int = 0,
) -> dict[str, str]:
    """Fit a tiny LogisticRegression on synthetic data; persist via joblib."""
    x, y = make_classification(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=2,
        n_redundant=0,
        random_state=random_state,
    )
    model = LogisticRegression(max_iter=200).fit(x, y)
    joblib.dump(model, artifact_path)
    digest = hashlib.sha256(Path(artifact_path).read_bytes()).hexdigest()
    return {"artifact_path": artifact_path, "content_hash": digest}


def _make_register_tool(registry: ModelRegistry) -> Any:
    """Bind a :class:`ModelRegistry` into an ``ml.register`` ``@tool``.

    The registry is closed over rather than passed as a tool input -- it
    is engine infrastructure, not part of the wire-serializable IR. This
    matches the design §3.9.4 recipe: tool inputs are JSON-encodable
    state fields; runtime services (registry, capability gate, audit
    sink) are injected at execution time.
    """

    @tool(
        name="register",
        namespace="ml",
        version="1.0.0",
        side_effects=SideEffects.write,  # writes a row into SQLite
        replay_policy=ReplayPolicy.must_stub,
    )
    async def ml_register(
        *,
        model_id: str,
        version: str,
        runtime: str,
        file_uri: str,
        content_hash: str,
    ) -> dict[str, str]:
        """Insert one row into the tiny model registry."""
        await registry.register(
            model_id=model_id,
            version=version,
            runtime=runtime,
            file_uri=file_uri,
            content_hash=content_hash,
        )
        return {"model_id": model_id, "version": version}

    return ml_register


# --------------------------------------------------------------------------- #
# Reference-IR shape sanity                                                   #
# --------------------------------------------------------------------------- #


def test_reference_yaml_shape_documents_subgraph_tool_and_ml_primitives() -> None:
    """The reference IR fixture wires sub-graph + tool + ml nodes per §3.9.4.

    Pins the FR-32 contract: no new node type -- only ``subgraph`` /
    ``tool`` / ``ml`` kinds, with ``side_effects=none`` on the inference
    node so replay re-executes natively (per FR-32 statement).
    """
    doc: dict[str, Any] = yaml.safe_load(_REFERENCE_YAML.read_text(encoding="utf-8"))

    kinds = {n["id"]: n["kind"] for n in doc["nodes"]}
    assert kinds["train_subgraph"] == "subgraph"
    assert kinds["register_node"] == "tool"
    assert kinds["infer_node"] == "ml"

    infer = next(n for n in doc["nodes"] if n["id"] == "infer_node")
    # The MLNode itself doesn't carry side_effects in spec -- the *contract*
    # is that ML inference is pure; replay re-executes natively (FR-32).
    assert infer["spec"]["runtime"] == "sklearn"

    # Tool primitives' side_effects are declared on the @tool decorator.
    assert ml_fit.spec.side_effects == SideEffects.write
    assert ml_fit.spec.replay_policy == ReplayPolicy.must_stub

    # ml.register is built per-registry (closure-bound); the spec shape is
    # identical regardless of the bound registry instance.
    sample_register = _make_register_tool(ModelRegistry(":memory:"))
    assert sample_register.spec.side_effects == SideEffects.write
    assert sample_register.spec.replay_policy == ReplayPolicy.must_stub


# --------------------------------------------------------------------------- #
# End-to-end: train -> register -> infer                                      #
# --------------------------------------------------------------------------- #


async def test_training_subgraph_produces_a_registered_model(tmp_path: Path) -> None:
    """FR-32 acceptance: the reference recipe produces a registered model.

    Executes the logical steps from ``training-subgraph.yaml`` against the
    real ``ModelRegistry`` + ``MLNode`` primitives. Done-when criterion:
    the model is registered (queryable by ``model_id`` / ``version``) and
    the registered artifact is loadable via ``MLNode``.
    """
    # ----- 1. train_subgraph: ml.fit @tool produces an artifact + hash --
    artifact = tmp_path / "logreg.joblib"
    fit_out = ml_fit(artifact_path=str(artifact), n_samples=64, n_features=4, random_state=0)
    _write_sklearn_sidecar(str(artifact))

    _assert_artifact_exists(fit_out["artifact_path"])
    assert len(fit_out["content_hash"]) == 64  # sha256 hex digest

    # ----- 2. register_node: ml.register @tool inserts into registry ----
    registry = ModelRegistry(tmp_path / "models.db")
    await registry.bootstrap()
    try:
        ml_register = _make_register_tool(registry)
        await ml_register(
            model_id="reference-logreg",
            version="1.0.0",
            runtime="sklearn",
            file_uri=artifact.as_uri(),
            content_hash=fit_out["content_hash"],
        )

        # Done-when: model is registered (queryable by model_id/version).
        entry = await registry.load("reference-logreg", "1.0.0")
        assert entry.model_id == "reference-logreg"
        assert entry.version == "1.0.0"
        assert entry.runtime == "sklearn"
        assert entry.content_hash == fit_out["content_hash"]

        # ----- 3. infer_node: MLNode wraps the registered artifact ----
        infer = MLNode(
            model_id="reference-logreg",
            version="1.0.0",
            runtime="sklearn",
            file_uri=artifact.as_uri(),
            allow_unsafe_pickle=True,
            input_field="x",
            output_field="y",
        )

        class _State:
            x = np.zeros((3, 4), dtype=np.float64)

        class _Ctx:
            run_id = "fr32-test"

        out = await infer.execute(_State(), _Ctx())  # type: ignore[arg-type]
        assert "y" in out
        assert out["y"].shape == (3,)
    finally:
        await registry.close()


# --------------------------------------------------------------------------- #
# Replay path: registry mismatch -> must-stub                                 #
# --------------------------------------------------------------------------- #


async def test_replay_path_handles_registry_mismatch_via_hash_gate(
    tmp_path: Path,
) -> None:
    """FR-32 statement: registry mismatch -> must-stub.

    If the registered ``content_hash`` no longer matches the artifact's
    bytes (e.g. someone re-trained and overwrote the file without a new
    version row), :meth:`ModelRegistry.load` raises
    :class:`IncompatibleModelHashError`. This is the signal the FR-21
    ``must-stub`` replay envelope catches to fall back to the recorded
    cassette rather than silently re-executing against a swapped artifact.
    """
    artifact = tmp_path / "model.joblib"
    fit_out = ml_fit(artifact_path=str(artifact), n_samples=32, n_features=3, random_state=1)

    registry = ModelRegistry(tmp_path / "models.db")
    await registry.bootstrap()
    try:
        await registry.register(
            model_id="reference-logreg",
            version="1.0.0",
            runtime="sklearn",
            file_uri=artifact.as_uri(),
            content_hash=fit_out["content_hash"],
        )

        # Simulate a model-version drift: rewrite the artifact bytes
        # without bumping the registry row. The next load() must refuse.
        _overwrite_artifact(artifact, b"corrupted-model-bytes")

        with pytest.raises(IncompatibleModelHashError) as excinfo:
            await registry.load("reference-logreg", "1.0.0")

        # The error carries the data the must-stub envelope needs to
        # decide whether to fall back to a recorded cassette.
        ctx = excinfo.value.context
        assert ctx["model_id"] == "reference-logreg"
        assert ctx["expected_sha256"] == fit_out["content_hash"]
        assert ctx["actual_sha256"] != fit_out["content_hash"]
    finally:
        await registry.close()
