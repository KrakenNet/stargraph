# SPDX-License-Identifier: Apache-2.0
"""Skillsmith run state — the shared spine plus the skill's domain output fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what a *skill bundle* contributes: its name + kind + one-line description, the
ordered node-class names that become the subgraph wiring, the capability
``requires`` and optional ``system_prompt`` the manifest carries, and the
``fixture`` (``inputs`` + ``expects``) the contract tier runs the assembled
subgraph against. Linear graph (triage → recall → build → record); the bounded
repair loop lives inside ``build``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    skill_name: str = ""
    kind: str = ""
    description: str = ""
    node_classes: list[str] = Field(default_factory=list[str])
    requires: list[str] = Field(default_factory=list[str])
    system_prompt: str = ""
    fixture: dict[str, Any] = Field(default_factory=dict)
