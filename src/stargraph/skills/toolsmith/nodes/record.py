# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`TOOL_SPEC`): on
success log the (spec → tool) trainset pair + land the files; on failure log a
summary reflexion lesson."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.toolsmith.nodes.build import TOOL_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=TOOL_SPEC)
