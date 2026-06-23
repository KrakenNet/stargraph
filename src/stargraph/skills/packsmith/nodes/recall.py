# SPDX-License-Identifier: Apache-2.0
"""Recall — gather grounding before the build (shared logic, bound to
:data:`PACK_SPEC`: reflexion lessons + CLIPS-pack/signing-contract RAG + model-decided
web)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecall
from stargraph.skills.packsmith.nodes.build import PACK_SPEC


class Recall(SmithRecall):
    def __init__(self) -> None:
        super().__init__(spec=PACK_SPEC)
