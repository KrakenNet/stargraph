# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.ml -- :class:`MLNode` for sklearn / xgboost / onnx (FR-30, design §3.9.1).

:class:`MLNode` wraps the runtime-specific loaders in
:mod:`stargraph.ml.loaders` and conforms to the
:class:`stargraph.nodes.NodeBase` Protocol so a model can be dropped into
a graph as a normal node. The default-deny pickle gate fires at
construction time (eager, not lazy) so the failure mode is identical
whether a graph definition is built up-front or lazily on first
execution -- there is no path where ``allow_unsafe_pickle=False`` plus
a sklearn ``file://`` URI builds a usable node.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from stargraph.errors import MLNodeError
from stargraph.ml import loaders
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


Runtime = Literal["sklearn", "xgboost", "onnx"]


class MLNode(NodeBase):
    """Graph node that runs a classical-ML model (sklearn / xgboost / onnx).

    Construction is eager-validated: the runtime is checked, the
    pickle gate fires for ``runtime="sklearn"`` when
    ``allow_unsafe_pickle=False``, and the underlying ONNX session (if
    any) is warmed via the module-scope cache so the first
    :meth:`execute` call doesn't pay the cold-cache cost.

    :param model_id: Unique identifier within the model registry.
    :param version: Semver-ish version string -- forms the cache key
        with ``model_id`` for the ONNX session pool.
    :param runtime: One of ``"sklearn"``, ``"xgboost"``, or ``"onnx"``.
    :param file_uri: ``file://`` URI of the model bytes. Required for
        eager loading; ``None`` defers to a registry lookup at execute
        time (Phase 3 stub -- the registry lands in task 3.38).
    :param allow_unsafe_pickle: Default-deny gate for the sklearn
        pickle path (FR-30 antipattern guard #4). Has no effect on the
        xgboost or onnx runtimes.
    :param expected_sha256: Optional pinned SHA-256 of the model file;
        verified before any unpickling step.
    :param input_field: Name of the state field to read inference
        inputs from. Default ``"x"``.
    :param output_field: Name of the state field to write predictions
        to. Default ``"y"``.
    """

    def __init__(
        self,
        *,
        model_id: str,
        version: str,
        runtime: Runtime,
        file_uri: str | None = None,
        allow_unsafe_pickle: bool = False,
        expected_sha256: str | None = None,
        input_field: str = "x",
        output_field: str = "y",
    ) -> None:
        if runtime not in ("sklearn", "xgboost", "onnx"):
            raise MLNodeError(
                f"unsupported runtime {runtime!r}; expected one of 'sklearn', 'xgboost', 'onnx'",
                model_id=model_id,
                version=version,
                runtime=runtime,
            )

        self.model_id = model_id
        self.version = version
        self.runtime: Runtime = runtime
        self.file_uri = file_uri
        self.allow_unsafe_pickle = allow_unsafe_pickle
        self.expected_sha256 = expected_sha256
        self.input_field = input_field
        self.output_field = output_field

        # Eager pickle gate -- fires *before* any joblib.load call so
        # the test contract `pickle disabled.*set allow_unsafe_pickle=True`
        # holds whether the loader is invoked at construction or at
        # first execute().
        if runtime == "sklearn" and not allow_unsafe_pickle:
            raise MLNodeError(
                "pickle disabled; set allow_unsafe_pickle=True to opt in",
                model_id=model_id,
                version=version,
                file_uri=file_uri,
                runtime="sklearn",
            )

        # Eagerly resolve the model so failures (sidecar skew, hash
        # mismatch, .bin xgboost, etc.) surface at definition time. ONNX
        # uses the shared session cache; sklearn / xgboost hold the
        # loaded estimator on the instance.
        self._model: Any = None
        self._onnx_session: Any = None
        if file_uri is not None:
            self._load()

    # -- loading -----------------------------------------------------

    def _load(self) -> None:
        """Resolve the underlying model object based on ``self.runtime``."""
        assert self.file_uri is not None
        if self.runtime == "sklearn":
            self._model = loaders.load_sklearn_model(
                model_id=self.model_id,
                version=self.version,
                file_uri=self.file_uri,
                allow_unsafe_pickle=self.allow_unsafe_pickle,
                expected_sha256=self.expected_sha256,
            )
        elif self.runtime == "xgboost":
            self._model = loaders.load_xgboost_model(
                model_id=self.model_id,
                version=self.version,
                file_uri=self.file_uri,
            )
        else:  # onnx
            self._onnx_session = loaders.get_onnx_session(
                model_id=self.model_id,
                version=self.version,
                file_uri=self.file_uri,
            )

    # -- execute -----------------------------------------------------

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Run the model on the configured input field.

        Inference is offloaded to a worker thread via
        :func:`asyncio.to_thread` so the event loop is never blocked by
        a sync ``predict`` call. The output is wrapped in a single-key
        dict keyed by ``self.output_field`` so the field-merge registry
        (FR-11) writes it back into state cleanly.
        """
        del ctx  # unused in Phase 3; kept for interface symmetry
        inputs: object = getattr(state, self.input_field)

        outputs = await asyncio.to_thread(self._predict, inputs)
        return {self.output_field: outputs}

    def _predict(self, inputs: Any) -> Any:
        """Synchronous inference dispatch (called from a worker thread)."""
        if self.runtime == "onnx":
            session = self._onnx_session
            if session is None:
                raise MLNodeError(
                    "onnx session not initialised",
                    model_id=self.model_id,
                    version=self.version,
                    runtime="onnx",
                )
            import numpy as np

            input_name = session.get_inputs()[0].name
            # A single feature vector arrives rank-1 (``list[float]`` from a
            # state field); ONNX ``[None, N]`` inputs need a batch axis. Coerce
            # to float32 and add the axis when missing so a one-sample run works
            # without the caller pre-batching. Already-batched rank-2 inputs
            # pass through unchanged.
            arr = np.asarray(inputs, dtype=np.float32)
            if arr.ndim == 1:
                arr = arr[np.newaxis, :]
            result = session.run(None, {input_name: arr})[0]
            # Unwrap the single-sample batch back to a JSON-serializable scalar
            # / row so the field-merge writes a plain Python value into typed
            # state (numpy scalars/arrays are not JSON-serializable). Multi-row
            # batches are returned as a list.
            out = np.asarray(result)
            if out.shape[0] == 1:
                return out[0].tolist()
            return out.tolist()

        model = self._model
        if model is None:
            raise MLNodeError(
                "model not loaded",
                model_id=self.model_id,
                version=self.version,
                runtime=self.runtime,
            )
        # sklearn + xgboost both expose .predict(...)
        return model.predict(inputs)
