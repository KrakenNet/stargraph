# SPDX-License-Identifier: Apache-2.0
"""Recall — gather grounding before the build (shared logic, bound to
:data:`ML_SPEC`: reflexion lessons + MLNode/loader-contract RAG + model-decided
web)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecall
from stargraph.skills.mlsmith.nodes.build import ML_SPEC


class Recall(SmithRecall):
    def __init__(self) -> None:
        super().__init__(spec=ML_SPEC)
