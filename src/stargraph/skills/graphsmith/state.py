# SPDX-License-Identifier: Apache-2.0
"""Graphsmith run state — the shared spine plus the graph's domain output fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what a *graph bundle* contributes: its id, the ordered node-class names that
become the wiring, and the ``fixture`` (``inputs`` + ``expects``) the contract
tier runs the assembled graph against. Linear graph (triage → recall → build →
record); the bounded repair loop lives inside ``build``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    graph_id: str = ""
    node_classes: list[str] = Field(default_factory=list[str])
    fixture: dict[str, Any] = Field(default_factory=dict)
