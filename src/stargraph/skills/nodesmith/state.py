# SPDX-License-Identifier: Apache-2.0
"""Nodesmith run state — the shared spine plus the node's domain output fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what a *node* contributes: its class name, declared reads/writes, and the
fixture the contract tier runs against. The graph runs linearly (triage → recall
→ build → record); the bounded repair loop lives inside ``build``, so no
rule-routing fields are needed.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    node_name: str | None = None
    class_name: str = ""
    reads: list[str] = Field(default_factory=list[str])
    writes: list[str] = Field(default_factory=list[str])
    fixture: dict[str, Any] = Field(default_factory=dict)
