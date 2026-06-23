# SPDX-License-Identifier: Apache-2.0
"""Recall — gather grounding before the build (shared logic, bound to
:data:`ADAPTER_SPEC`: reflexion lessons + adapter-corpus RAG + model-decided web)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecall
from stargraph.skills.adaptersmith.nodes.build import ADAPTER_SPEC


class Recall(SmithRecall):
    def __init__(self) -> None:
        super().__init__(spec=ADAPTER_SPEC)
