# SPDX-License-Identifier: Apache-2.0
"""Recall — gather grounding before the build (shared logic, bound to
:data:`STORE_SPEC`: reflexion lessons + store-corpus RAG + model-decided web)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecall
from stargraph.skills.storesmith.nodes.build import STORE_SPEC


class Recall(SmithRecall):
    def __init__(self) -> None:
        super().__init__(spec=STORE_SPEC)
