# SPDX-License-Identifier: Apache-2.0
"""Plan — decompose the request into a typed build manifest (the LLM step)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import NodeBase
from stargraph.skills.foundry.program import default_planner

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext
    from stargraph.skills.foundry.manifest import BuildManifest

__all__ = ["Plan"]


class Plan(NodeBase):
    """Turn ``state.request`` into ``state.manifest``. ``planner`` is the seam tests
    pin to a deterministic stub; it defaults to the live LM-backed planner."""

    def __init__(self, *, planner: Callable[[str], BuildManifest] | None = None) -> None:
        self._planner = planner or default_planner

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        request = str(getattr(state, "request", "") or "")
        if not request.strip():
            raise ValueError("request is required: describe the stargraph to build")
        manifest = self._planner(request)
        return {"manifest": list(manifest.items)}
