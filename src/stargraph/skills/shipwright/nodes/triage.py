# SPDX-License-Identifier: Apache-2.0
"""TriageGate — validates inputs and emits the routing fact for mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class TriageGate(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        mode = state.mode  # type: ignore[attr-defined]
        brief = state.brief  # type: ignore[attr-defined]
        target_path = state.target_path  # type: ignore[attr-defined]
        if mode == "new" and not brief:
            raise ValueError("brief is required for mode=new")
        if mode == "fix" and not target_path:
            raise ValueError("target_path is required for mode=fix")
        return {"mode": mode}
