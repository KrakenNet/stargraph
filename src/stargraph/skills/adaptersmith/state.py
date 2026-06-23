# SPDX-License-Identifier: Apache-2.0
"""Adaptersmith run state — the shared spine plus the adapter's domain fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds
only what an *adapter* contributes: its name, its namespace, and the fixture the
contract tier may carry (the gate embeds its own literals, so the fixture is
advisory). Linear graph (triage → recall → build → record); the bounded repair
loop lives inside ``build``, so no rule-routing fields are needed.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    adapter_name: str = ""
    namespace: str = "mcp"
    fixture: dict[str, Any] = Field(default_factory=dict)
