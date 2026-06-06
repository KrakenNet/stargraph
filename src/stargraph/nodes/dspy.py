# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.dspy -- :class:`DSPyNode` (FR-5, design §3.3.1, §5).

:class:`DSPyNode` wraps a DSPy module so the stargraph execution loop can
dispatch it like any other :class:`~stargraph.nodes.base.NodeBase`. Pydantic
run-state fields are projected to DSPy signature inputs (and the module's
outputs projected back) via the user-supplied ``signature_map``.

The force-loud adapter (``JSONAdapter(use_native_function_calling=True)``
+ ``ChatAdapter(use_json_adapter_fallback=False)`` + the
``_LoudFallbackFilter`` installed on ``dspy.adapters.json_adapter``) is
wired in by :func:`stargraph.adapters.dspy.bind`; this module never silently
swaps adapters -- the FR-6 seam guarantees any latent fallback raises
:class:`~stargraph.errors.AdapterFallbackError` instead.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from stargraph.adapters.dspy import FALLBACK_NEEDLE
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.adapters.dspy import SignatureMap


_FALLBACK_LOGGER = logging.getLogger("dspy.adapters.json_adapter")


class DSPyNode(NodeBase):
    """Stargraph node wrapping a DSPy module (FR-5).

    Constructed via :func:`stargraph.adapters.dspy.bind`, never directly --
    ``bind`` is responsible for installing the force-loud
    :class:`~stargraph.adapters.dspy._LoudFallbackFilter` on the DSPy
    json-adapter logger before any module call can occur.

    :param module: The wrapped ``dspy.Module``-compatible callable. Kept
        as :class:`~typing.Any` at the seam so the FR-6 integration test
        can pass an inert fixture and surface the loud-fail behaviour
        end-to-end without standing up a real LM.
    :param adapter: The default ``dspy.JSONAdapter`` (force-loud config).
    :param chat_adapter: The chat-style ``dspy.ChatAdapter`` with
        ``use_json_adapter_fallback=False``.
    :param signature_map: Mapping from stargraph state-field names to DSPy
        signature input/output names. Resolved on each call.
    """

    def __init__(
        self,
        *,
        module: Any,
        adapter: Any,
        chat_adapter: Any,
        signature_map: SignatureMap | Any,
    ) -> None:
        self._module: Any = module
        self._adapter: Any = adapter
        self._chat_adapter: Any = chat_adapter
        self._signature_map: Any = signature_map

    def acall(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Invoke the wrapped DSPy module with ``inputs`` (FR-5/FR-6).

        On *any* invocation failure -- whether a Pydantic constraint
        violation in the structured-output parser, a malformed signature
        map, or an outright TypeError because the wrapped object is not
        a DSPy module -- the call emits the canonical DSPy fallback
        warning to ``dspy.adapters.json_adapter``. The
        :class:`_LoudFallbackFilter` installed by
        :func:`stargraph.adapters.dspy.bind` converts that warning into
        :class:`~stargraph.errors.AdapterFallbackError`, so this method
        *never* returns from a fallback path -- the only success path is
        a clean module call.
        """
        try:
            mapped_inputs = self._project_inputs(inputs)
            result = self._module(**mapped_inputs)
        except Exception as err:
            # Trip the force-loud seam: the filter on the json_adapter
            # logger raises AdapterFallbackError from inside .filter().
            _FALLBACK_LOGGER.warning(FALLBACK_NEEDLE)
            # If the filter is somehow absent (caller bypassed bind),
            # surface the fallback explicitly so the seam never silents.
            from stargraph.errors import AdapterFallbackError

            raise AdapterFallbackError(
                FALLBACK_NEEDLE,
                adapter="dspy",
            ) from err
        return self._project_outputs(result)

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Execute the node against ``state`` (NodeBase contract, FR-1).

        Projects ``state`` fields named in ``signature_map`` to DSPy
        signature inputs, calls the module, and returns the dict of
        state-field outputs the execution loop merges back into the run
        state via the field-merge registry (FR-11).
        """
        del ctx  # Phase-2: no per-run context fields read by DSPyNode yet
        inputs = {k: getattr(state, k) for k in self._iter_input_keys()}
        return self.acall(inputs)

    # ----------------------------------------------------------------------
    # internals
    # ----------------------------------------------------------------------

    def _project_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Map stargraph state-field inputs to DSPy signature input names.

        When ``signature_map`` is a plain ``dict[str, str]``, each stargraph
        key is renamed to its DSPy counterpart; non-mapping signature
        maps are passed through unchanged so this method stays inert for
        the FR-6 integration test fixture (which uses ``object()``).
        """
        sig_map = self._signature_map
        if isinstance(sig_map, dict):
            sig_map_typed = cast("dict[str, str]", sig_map)
            return {sig_map_typed.get(k, k): v for k, v in inputs.items()}
        return dict(inputs)

    def _project_outputs(self, result: Any) -> dict[str, Any]:
        """Map DSPy module outputs back to stargraph state-field names.

        DSPy ``Prediction`` objects expose attributes per signature
        output; ``dict``-shaped results are passed through. Anything
        else is wrapped under ``"output"`` so the merge contract still
        receives a dict (FR-11).
        """
        if isinstance(result, dict):
            return cast("dict[str, Any]", result).copy()
        as_dict = getattr(result, "__dict__", None)
        if isinstance(as_dict, dict) and as_dict:
            return cast("dict[str, Any]", as_dict).copy()
        return {"output": result}

    def _iter_input_keys(self) -> list[str]:
        """List stargraph state-field keys to project as inputs.

        With a ``dict`` signature map, the keys are the stargraph-side
        names. Otherwise we hand back an empty list -- the module call
        is then driven entirely by ``acall(inputs=...)`` callers.
        """
        sig_map = self._signature_map
        if isinstance(sig_map, dict):
            sig_map_typed = cast("dict[str, str]", sig_map)
            return list(sig_map_typed.keys())
        return []


__all__ = ["DSPyNode"]
