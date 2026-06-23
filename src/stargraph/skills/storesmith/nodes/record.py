# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`STORE_SPEC`): on
success log the (spec → store) trainset pair + land the files; on failure log a
summary reflexion lesson."""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.storesmith.nodes.build import STORE_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=STORE_SPEC)
