# SPDX-License-Identifier: Apache-2.0
"""TriageGate — reject empty briefs before spending an LLM call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class TriageGate(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = getattr(state, "brief", None)
        if not brief or not str(brief).strip():
            raise ValueError("brief is required: describe the node to build")
        return {}
