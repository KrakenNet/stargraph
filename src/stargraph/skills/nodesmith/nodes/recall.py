# SPDX-License-Identifier: Apache-2.0
"""Recall — load reflexion lessons relevant to this brief (idea 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.nodesmith import _ledger

if TYPE_CHECKING:
    from pydantic import BaseModel


class Recall(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = str(getattr(state, "brief", "") or "")
        lessons = _ledger.recall_lessons(brief, limit=3)
        return {"recalled_lessons": lessons}
