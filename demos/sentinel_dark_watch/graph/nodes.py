# SPDX-License-Identifier: Apache-2.0
"""Sentinel Dark Watch — graph node implementations.

Each node subclasses :class:`harbor.nodes.base.NodeBase` and returns a
dict of state-field mutations merged by the execution loop.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import uuid
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


# ---------------------------------------------------------------------------
# YOLO OBB Inference
# ---------------------------------------------------------------------------

# Tile size for YOLO inference patches
_PATCH_SIZE = 640
_OVERLAP_FRAC = 0.1


def _tile_image(img_array: Any, patch_size: int, overlap: float) -> list[tuple[Any, int, int]]:
    """Tile a (H, W, C) or (C, H, W) array into overlapping patches.

    Returns list of (patch_array, row_offset, col_offset).
    """
    # Ensure H, W, C layout for slicing
    if img_array.ndim == 3 and img_array.shape[0] <= 4:
        # (C, H, W) → (H, W, C)
        img_array = img_array.transpose(1, 2, 0)

    h, w = img_array.shape[:2]
    stride = int(patch_size * (1.0 - overlap))
    patches: list[tuple[Any, int, int]] = []
    for r in range(0, max(h - patch_size + 1, 1), stride):
        for c in range(0, max(w - patch_size + 1, 1), stride):
            patch = img_array[r : r + patch_size, c : c + patch_size]
            patches.append((patch, r, c))
    return patches


def _pixel_to_geo(
    px_x: float,
    px_y: float,
    affine: Any,
) -> tuple[float, float]:
    """Convert pixel coords to geographic coords via affine transform."""
    geo_x = affine.c + px_x * affine.a + px_y * affine.b
    geo_y = affine.f + px_x * affine.d + px_y * affine.e
    return geo_x, geo_y


def _decode_obb(
    raw_output: Any,
    row_off: int,
    col_off: int,
    affine: Any,
    conf_threshold: float = 0.25,
) -> list[dict[str, Any]]:
    """Decode ONNX OBB output: [x_ctr, y_ctr, w, h, angle, conf, class].

    Returns list of detection dicts with geo-coordinates.
    """
    import numpy as np

    detections: list[dict[str, Any]] = []
    if raw_output is None or len(raw_output) == 0:
        return detections

    arr = np.asarray(raw_output)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)

    for row in arr:
        if len(row) < 7:
            continue
        x_ctr, y_ctr, w, h, angle, conf, _cls = row[:7]
        if conf < conf_threshold:
            continue

        # Patch-local pixel → image-level pixel
        img_x = col_off + x_ctr
        img_y = row_off + y_ctr

        # Geo center
        geo_lon, geo_lat = _pixel_to_geo(img_x, img_y, affine)

        # Compute OBB corners in geo-coords
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        half_w, half_h = w / 2, h / 2
        corners_px = [
            (-half_w, -half_h),
            (half_w, -half_h),
            (half_w, half_h),
            (-half_w, half_h),
        ]
        geo_corners: list[list[float]] = []
        for dx, dy in corners_px:
            rx = cos_a * dx - sin_a * dy
            ry = sin_a * dx + cos_a * dy
            gx, gy = _pixel_to_geo(img_x + rx, img_y + ry, affine)
            geo_corners.append([gx, gy])

        detections.append({
            "detection_id": str(uuid.uuid4()),
            "geo_lat": geo_lat,
            "geo_lon": geo_lon,
            "confidence": float(conf),
            "obb_corners": geo_corners,
            "vessel_length_m": float(max(w, h)),  # rough proxy
        })
    return detections


class YOLOInferenceNode(NodeBase):
    """Load GeoTIFF, tile into patches, run ONNX OBB inference, geo-transform."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        tile = state.current_tile  # type: ignore[attr-defined]

        # Guard: rasterio / onnxruntime may not be installed (POC)
        try:
            import numpy as np
            import rasterio  # noqa: F811
        except ImportError:
            log.warning("rasterio/numpy not installed — returning empty detections")
            return {"raw_detections": [], "pipeline_phase": "detection"}

        file_path = tile.file_path
        if not file_path or not Path(file_path).exists():
            log.warning("Tile file not found: %s — returning empty detections", file_path)
            return {"raw_detections": [], "pipeline_phase": "detection"}

        # 1. Load GeoTIFF and extract affine transform
        with rasterio.open(file_path) as src:
            img = src.read()  # (C, H, W)
            affine = src.transform

        # 2. Tile into 640x640 patches with 10% overlap
        patches = _tile_image(img, _PATCH_SIZE, _OVERLAP_FRAC)

        # 3. Two-step ONNX session acquisition via ModelRegistry
        try:
            from harbor.ml.loaders import get_onnx_session
            from harbor.ml.registry import ModelRegistry

            registry = ModelRegistry()
            entry = await registry.load_alias("sdw-detector", "production")
            session = get_onnx_session(
                model_id=entry.model_id,
                version=entry.version,
                file_uri=entry.file_uri,
            )
        except Exception:
            log.warning("ONNX session unavailable — returning empty detections")
            return {"raw_detections": [], "pipeline_phase": "detection"}

        # 4. Run inference per patch (offloaded to thread)
        input_name = session.get_inputs()[0].name
        all_detections: list[dict[str, Any]] = []

        for patch, r_off, c_off in patches:
            # Ensure (1, 3, H, W) float32
            if patch.ndim == 2:
                patch = np.stack([patch] * 3, axis=-1)
            if patch.shape[-1] <= 4:
                # (H, W, C) → (C, H, W)
                patch = patch.transpose(2, 0, 1)
            blob = np.expand_dims(patch.astype(np.float32) / 255.0, axis=0)

            raw = await asyncio.to_thread(session.run, None, {input_name: blob})

            # 5-6. Decode OBB + transform to geo-coords
            if raw and len(raw) > 0:
                dets = _decode_obb(raw[0], r_off, c_off, affine)
                for d in dets:
                    d["tile_id"] = tile.tile_id
                all_detections.extend(dets)

        # 7. Build Detection objects
        from demos.sentinel_dark_watch.graph.state import Detection

        detections = [Detection(**d) for d in all_detections]

        return {"raw_detections": detections, "pipeline_phase": "detection"}
