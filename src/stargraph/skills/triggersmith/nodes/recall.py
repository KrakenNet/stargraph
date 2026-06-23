# SPDX-License-Identifier: Apache-2.0
"""Recall — gather grounding before the build (shared logic, bound to
:data:`TRIGGER_SPEC`: reflexion lessons + trigger-corpus RAG + model-decided web)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecall
from stargraph.skills.triggersmith.nodes.build import TRIGGER_SPEC


class Recall(SmithRecall):
    def __init__(self) -> None:
        super().__init__(spec=TRIGGER_SPEC)
