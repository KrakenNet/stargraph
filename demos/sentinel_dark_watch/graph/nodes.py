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


# ---------------------------------------------------------------------------
# Cross-Tile NMS (Non-Maximum Suppression)
# ---------------------------------------------------------------------------


def _obb_polygon(corners: list[list[float]]) -> list[tuple[float, float]]:
    """Return closed polygon from OBB corner list."""
    pts = [(c[0], c[1]) for c in corners]
    if pts and pts[-1] != pts[0]:
        pts.append(pts[0])
    return pts


def _shoelace_area(poly: list[tuple[float, float]]) -> float:
    """Signed area of a simple polygon via the shoelace formula."""
    n = len(poly)
    if n < 4:  # need >= 3 unique + closing duplicate
        return 0.0
    area = 0.0
    for i in range(n - 1):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def _line_intersection(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> tuple[float, float] | None:
    """Intersection point of line segments p1-p2 and p3-p4 (or None)."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    cross = d1x * d2y - d1y * d2x
    if abs(cross) < 1e-12:
        return None
    t = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / cross
    u = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / cross
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (p1[0] + t * d1x, p1[1] + t * d1y)
    return None


def _point_in_convex(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    """Test if *pt* is inside a convex polygon (edges in consistent winding)."""
    n = len(poly) - 1  # last == first
    if n < 3:
        return False
    sign = None
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        cross = (x1 - x0) * (pt[1] - y0) - (y1 - y0) * (pt[0] - x0)
        if abs(cross) < 1e-12:
            continue
        s = cross > 0
        if sign is None:
            sign = s
        elif s != sign:
            return False
    return True


def _polygon_intersection_area(
    poly_a: list[tuple[float, float]],
    poly_b: list[tuple[float, float]],
) -> float:
    """Intersection area of two convex polygons using Sutherland-Hodgman clipping."""
    # Clip poly_a by each edge of poly_b
    output = list(poly_a[:-1])  # open polygon
    n_b = len(poly_b) - 1
    for i in range(n_b):
        if not output:
            return 0.0
        edge_s = poly_b[i]
        edge_e = poly_b[i + 1]
        inp = output
        output = []
        for j in range(len(inp)):
            cur = inp[j]
            nxt = inp[(j + 1) % len(inp)]
            cur_side = (edge_e[0] - edge_s[0]) * (cur[1] - edge_s[1]) - (edge_e[1] - edge_s[1]) * (cur[0] - edge_s[0])
            nxt_side = (edge_e[0] - edge_s[0]) * (nxt[1] - edge_s[1]) - (edge_e[1] - edge_s[1]) * (nxt[0] - edge_s[0])
            if cur_side >= 0:
                output.append(cur)
                if nxt_side < 0:
                    ix = _line_intersection(cur, nxt, edge_s, edge_e)
                    if ix:
                        output.append(ix)
            elif nxt_side >= 0:
                ix = _line_intersection(cur, nxt, edge_s, edge_e)
                if ix:
                    output.append(ix)
    if len(output) < 3:
        return 0.0
    closed = output + [output[0]]
    return _shoelace_area(closed)


def _rotated_iou(corners_a: list[list[float]], corners_b: list[list[float]]) -> float:
    """Compute IoU of two oriented bounding boxes given as corner coordinate lists."""
    poly_a = _obb_polygon(corners_a)
    poly_b = _obb_polygon(corners_b)
    area_a = _shoelace_area(poly_a)
    area_b = _shoelace_area(poly_b)
    if area_a < 1e-12 or area_b < 1e-12:
        # Fall back to centroid distance
        return 0.0
    inter = _polygon_intersection_area(poly_a, poly_b)
    union = area_a + area_b - inter
    if union < 1e-12:
        return 0.0
    return inter / union


class NMSDeduplicationNode(NodeBase):
    """Cross-tile NMS using rotated IoU on geo-coordinates.

    Removes duplicate detections from overlapping tile regions.
    Configurable IoU threshold (default 0.5).  Pure geometry — no
    external dependencies.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        raw: list[Any] = list(state.raw_detections)  # type: ignore[attr-defined]
        if not raw:
            return {"detections": [], "detection_count": 0, "pipeline_phase": "nms"}

        iou_threshold = 0.5

        # Sort by confidence descending — keep higher-confidence detections
        raw.sort(key=lambda d: d.confidence, reverse=True)

        keep: list[Any] = []
        suppressed: set[int] = set()

        for i, det_i in enumerate(raw):
            if i in suppressed:
                continue
            keep.append(det_i)
            if not det_i.obb_corners:
                continue
            for j in range(i + 1, len(raw)):
                if j in suppressed:
                    continue
                det_j = raw[j]
                if not det_j.obb_corners:
                    continue
                iou = _rotated_iou(det_i.obb_corners, det_j.obb_corners)
                if iou >= iou_threshold:
                    suppressed.add(j)

        return {
            "detections": keep,
            "detection_count": len(keep),
            "pipeline_phase": "nms",
        }
