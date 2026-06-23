# SPDX-License-Identifier: Apache-2.0
"""Assemble — place the spine + capabilities into one runnable Stargraph dir."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import NodeBase
from stargraph.skills.foundry.assemble import assemble

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext

__all__ = ["Assemble"]


class Assemble(NodeBase):
    """Compose ``state.built`` into ``output_dir/assembled/`` (graph + capabilities
    + ``assembly.yaml``); writes ``graph_path`` / ``assembled_dir`` to state."""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        built = list(getattr(state, "built", []))
        output_dir = str(getattr(state, "output_dir", "") or "")
        return assemble(built, output_dir=output_dir)
