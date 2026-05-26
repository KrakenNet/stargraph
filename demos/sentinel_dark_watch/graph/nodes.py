# SPDX-License-Identifier: Apache-2.0
"""Sentinel Dark Watch — graph node implementations.

Each node subclasses :class:`harbor.nodes.base.NodeBase` and returns a
dict of state-field mutations merged by the execution loop.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harbor.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

log = logging.getLogger(__name__)


class PassthroughNode(NodeBase):
    """No-op node used for branch_resp_review and action_done."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        return {}


class SARIngestNode(NodeBase):
    """Pop next tile from queue, fetch metadata from PostGIS, validate file."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        tile_queue: list[str] = list(state.tile_queue)  # type: ignore[attr-defined]
        tiles_failed: int = state.tiles_failed  # type: ignore[attr-defined]
        failure_threshold: int = state.failure_threshold  # type: ignore[attr-defined]

        if not tile_queue:
            return {"last_error": "tile_queue empty", "pipeline_phase": "ingest"}

        tile_id = tile_queue.pop(0)

        # Query PostGIS sar_tiles table for tile metadata
        try:
            import asyncpg  # noqa: F811

            dsn = os.environ.get(
                "POSTGRES_DSN",
                "postgresql://harbor:harbor@localhost:5441/sdw",
            )
            conn = await asyncpg.connect(dsn)
            try:
                row = await conn.fetchrow(
                    "SELECT scene_id, file_path, acquired_at, "
                    "ST_AsText(bounds) AS bounds_wkt "
                    "FROM sar_tiles WHERE tile_id = $1",
                    tile_id,
                )
            finally:
                await conn.close()
        except Exception:
            log.warning("PostGIS unavailable — using stub tile metadata for %s", tile_id)
            row = None

        if row is not None:
            file_path = row["file_path"]
            if not Path(file_path).exists():
                tiles_failed += 1
                patch: dict[str, Any] = {
                    "tile_queue": tile_queue,
                    "tiles_failed": tiles_failed,
                }
                if tiles_failed >= failure_threshold:
                    patch["last_error"] = (
                        f"failure_threshold reached ({tiles_failed}/{failure_threshold})"
                    )
                return patch

            from demos.sentinel_dark_watch.graph.state import TileMetadata

            tile_meta = TileMetadata(
                tile_id=tile_id,
                scene_id=row["scene_id"],
                file_path=file_path,
                timestamp=str(row["acquired_at"]),
                bounds_wkt=row["bounds_wkt"] or "",
            )
        else:
            # Stub metadata when PostGIS is unavailable (POC)
            from demos.sentinel_dark_watch.graph.state import TileMetadata

            tile_meta = TileMetadata(tile_id=tile_id)

        return {
            "tile_queue": tile_queue,
            "current_tile": tile_meta,
            "current_tile_id": tile_id,
            "pipeline_phase": "ingest",
        }
