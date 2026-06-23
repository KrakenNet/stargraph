# SPDX-License-Identifier: Apache-2.0
"""Recall — gather grounding before the build (shared logic, bound to
:data:`PLUGIN_SPEC`: reflexion lessons + plugin/@tool-contract RAG + model-decided
web)."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecall
from stargraph.skills.pluginsmith.nodes.build import PLUGIN_SPEC


class Recall(SmithRecall):
    def __init__(self) -> None:
        super().__init__(spec=PLUGIN_SPEC)
