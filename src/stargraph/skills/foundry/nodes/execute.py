# SPDX-License-Identifier: Apache-2.0
"""Execute — run each manifest item through its smith; collect landed artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import NodeBase
from stargraph.skills._smith.nodes import snake
from stargraph.skills.foundry.dispatch import default_executor
from stargraph.skills.foundry.manifest import ManifestItem

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext
    from stargraph.skills.foundry.dispatch import Executor

__all__ = ["Execute"]


class Execute(NodeBase):
    """Build every manifest item, each into its own ``output_dir/build/<name>/``.

    ``executor`` is the seam tests pin to drive smiths with a stubbed generator; it
    defaults to the live executor (real lifecycle, real LM)."""

    def __init__(self, *, executor: Executor | None = None) -> None:
        self._executor = executor or default_executor

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        output_dir = str(getattr(state, "output_dir", "") or "")
        model_id = str(getattr(state, "model_id", "") or "")
        build_root = Path(output_dir) / "build"
        built: list[dict[str, Any]] = []
        for raw in list(getattr(state, "manifest", [])):
            item = raw if isinstance(raw, ManifestItem) else ManifestItem.model_validate(raw)
            item_out = str(build_root / snake(item.name))
            built.append(await self._executor(item, output_dir=item_out, model_id=model_id))
        return {"built": built}
