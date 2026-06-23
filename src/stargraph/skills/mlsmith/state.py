# SPDX-License-Identifier: Apache-2.0
"""ML smith run state — the shared spine plus the model node's domain fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what a *model node* contributes: its name, the ``runtime`` (``sklearn`` or
``onnx``) the gate loads it under, the ``input_field``/``output_field`` the MLNode
reads inputs from and writes predictions to, and the ``fixture`` (an input vector +
the expected prediction) the contract tier drives the constructed MLNode against.
Linear graph (triage → recall → build → record); the bounded repair loop lives
inside ``build``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    model_name: str = ""
    runtime: str = ""
    input_field: str = "x"
    output_field: str = "y"
    fixture: dict[str, Any] = Field(default_factory=dict)
