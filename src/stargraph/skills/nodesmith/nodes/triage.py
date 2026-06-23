# SPDX-License-Identifier: Apache-2.0
"""TriageGate — reject empty briefs before spending an LLM call (shared logic,
bound to :data:`NODE_SPEC`)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithTriage
from stargraph.skills.nodesmith.nodes.build import NODE_SPEC


class TriageGate(SmithTriage):
    def __init__(self) -> None:
        super().__init__(spec=NODE_SPEC)
