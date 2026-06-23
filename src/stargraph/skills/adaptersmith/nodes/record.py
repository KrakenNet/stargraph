# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`ADAPTER_SPEC`): on
success log the (spec → adapter) trainset pair + land the files; on failure log a
summary reflexion lesson."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.adaptersmith.nodes.build import ADAPTER_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=ADAPTER_SPEC)
