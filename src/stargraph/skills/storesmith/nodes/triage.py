# SPDX-License-Identifier: Apache-2.0
"""TriageGate — reject empty briefs before spending an LLM call (shared logic,
bound to :data:`STORE_SPEC`)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithTriage
from stargraph.skills.storesmith.nodes.build import STORE_SPEC


class TriageGate(SmithTriage):
    def __init__(self) -> None:
        super().__init__(spec=STORE_SPEC)
