# SPDX-License-Identifier: Apache-2.0
"""Storesmith run state — the shared spine plus the store's domain output fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what a *store* contributes: its class name and the fixture the contract tier
exercises it against (the targeted protocol is fixed to ``"doc"``). Linear graph
(triage → recall → build → record); the bounded repair loop lives inside
``build``, so no rule-routing fields are needed.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    class_name: str = ""
    fixture: dict[str, Any] = Field(default_factory=dict)
