# SPDX-License-Identifier: Apache-2.0
"""Pack smith run state — the shared spine plus the rule pack's domain fields.

The generic fields (brief/model_id/output_dir, recalled grounding, build outputs,
landed path) live in :class:`stargraph.skills._smith.state.SmithState`; this adds only
what a *rule pack* contributes: its name, the ``flavor`` (governance/routing), the
``input_template``/``output_template`` CLIPS deftemplate names the rules read facts from
and assert actions to, and the ``fixture`` (an input fact + the expected action) the
contract tier fires the loaded engine against. Linear graph (triage → recall → build →
record); the bounded repair loop lives inside ``build``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stargraph.skills._smith.state import SmithState, VerifierResult

__all__ = ["State", "VerifierResult"]


class State(SmithState):
    pack_name: str = ""
    flavor: str = "governance"
    input_template: str = ""
    output_template: str = ""
    fixture: dict[str, Any] = Field(default_factory=dict)
