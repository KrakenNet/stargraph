# SPDX-License-Identifier: Apache-2.0
"""Foundry run state — the orchestrator graph's spine.

A plain :class:`pydantic.BaseModel` (the foundry is a graph, not a smith — it
keeps no ledger): the ``request`` goes in, ``plan`` fills ``manifest``,
``execute`` fills ``built``, ``assemble`` fills ``assembled_dir`` / ``graph_path``,
and ``verify`` fills ``run_status`` / ``verified``. ``output_dir`` is where every
artifact lands; ``model_id`` is threaded to each smith.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from stargraph.skills.foundry.manifest import ManifestItem

__all__ = ["State"]


class State(BaseModel):
    request: str = ""
    model_id: str = ""
    output_dir: str = ""
    # plan → execute → assemble → verify each populate the next stretch of state.
    manifest: list[ManifestItem] = Field(default_factory=list[ManifestItem])
    built: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    assembled_dir: str = ""
    graph_path: str = ""
    run_status: str = ""
    verified: bool = False
    verify_detail: str = ""
