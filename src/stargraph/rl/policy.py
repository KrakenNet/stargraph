# SPDX-License-Identifier: Apache-2.0
"""stargraph.rl.policy -- :class:`PolicyNode`: ONNX policy inference as a graph node.

Thin specialization of :class:`~stargraph.nodes.ml.MLNode` over the ONNX
graphs published by :func:`stargraph.ml.export.export_sb3_policy` (W4):
``observation -> (actions, values, log_prob)``, actor evaluated
deterministically. All session handling (shared module-scope cache, eager
warm-up, CPU execution provider) is inherited from MLNode -- this class only
changes *what is returned*: the full SB3 triple mapped onto three state
fields instead of MLNode's single first-output field.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from stargraph.errors import MLNodeError
from stargraph.nodes.ml import MLNode

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext

__all__ = ["PolicyNode"]


class PolicyNode(MLNode):
    """MLNode over an exported SB3 policy: observation -> actions/values/log_prob.

    Runtime is pinned to ``"onnx"`` (the only policy publish path -- torch is
    never an MLNode runtime). A rank-1 observation gets the batch axis added,
    exactly like MLNode; single-sample batches are unwrapped so plain Python
    values land in state. ``values`` arrives as SB3 shapes it (a length-1 row
    per sample); ``log_prob`` unwraps to a scalar.

    :param model_id: Registry identifier of the exported policy.
    :param version: Registry version (cache key with ``model_id``).
    :param file_uri: ``file://`` URI of the ONNX bytes (from
        :class:`~stargraph.ml.registry.ModelEntry.file_uri`).
    :param observation_field: State field read as the observation.
    :param action_field: State field written with the deterministic action.
    :param value_field: State field written with the critic value.
    :param log_prob_field: State field written with the action log-probability.
    """

    def __init__(
        self,
        *,
        model_id: str,
        version: str,
        file_uri: str | None = None,
        observation_field: str = "observation",
        action_field: str = "action",
        value_field: str = "value",
        log_prob_field: str = "log_prob",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            runtime="onnx",
            file_uri=file_uri,
            input_field=observation_field,
            output_field=action_field,
        )
        self.observation_field = observation_field
        self.action_field = action_field
        self.value_field = value_field
        self.log_prob_field = log_prob_field

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        """Run the policy on ``state.<observation_field>``; write the SB3 triple."""
        del ctx
        observation: Any = getattr(state, self.observation_field)
        actions, values, log_prob = await asyncio.to_thread(self._predict_triple, observation)
        return {
            self.action_field: actions,
            self.value_field: values,
            self.log_prob_field: log_prob,
        }

    def _predict_triple(self, inputs: Any) -> tuple[Any, Any, Any]:
        """Session run returning all three policy outputs (worker thread)."""
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
        arr = np.asarray(inputs, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr[np.newaxis, :]
        results = session.run(None, {input_name: arr})
        if len(results) < 3:
            raise MLNodeError(
                f"policy graph returned {len(results)} outputs; expected "
                "(actions, values, log_prob) -- was it published via export_sb3_policy?",
                model_id=self.model_id,
                version=self.version,
                runtime="onnx",
            )

        def unbatch(result: Any) -> Any:
            out = np.asarray(result)
            if out.ndim > 0 and out.shape[0] == 1:
                return out[0].tolist()
            return out.tolist()

        actions, values, log_prob = results[0], results[1], results[2]
        return unbatch(actions), unbatch(values), unbatch(log_prob)
