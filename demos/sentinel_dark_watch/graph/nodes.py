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
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

from demos.sentinel_dark_watch.db import get_pg_dsn
from harbor.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
        try:
            tile_queue: list[str] = list(state.tile_queue)  # type: ignore[attr-defined]
            tiles_failed: int = state.tiles_failed  # type: ignore[attr-defined]
            failure_threshold: int = state.failure_threshold  # type: ignore[attr-defined]

            if not tile_queue:
                return {"last_error": "tile_queue empty", "pipeline_phase": "ingest"}

            tile_id = tile_queue.pop(0)

            # Query PostGIS sar_tiles table for tile metadata
            try:
                import asyncpg

                conn = await asyncpg.connect(get_pg_dsn())
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
                logger.warning("PostGIS unavailable — using stub tile metadata for %s", tile_id)
                row = None

            if row is not None:
                file_path = row["file_path"]
                if not Path(file_path).exists():  # noqa: ASYNC240
                    tiles_failed += 1
                    logger.warning(
                        "Tile file missing: %s (failed %d/%d)",
                        file_path,
                        tiles_failed,
                        failure_threshold,
                    )
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
        except Exception as exc:
            logger.exception("SARIngestNode failed: %s", exc)
            return {"last_error": f"SARIngestNode: {exc}", "pipeline_phase": "ingest"}


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
    """Decode Ultralytics YOLO OBB ONNX output.

    Output shape: (1, 6, N) for 1-class OBB where the 6 channels are
    [x_ctr, y_ctr, w, h, angle, conf]. Transpose to (N, 6) rows.
    """
    import numpy as np

    detections: list[dict[str, Any]] = []
    if raw_output is None:
        return detections

    arr = np.asarray(raw_output).squeeze()  # (6, N) or (7, N) etc.
    if arr.ndim == 1:
        return detections
    if arr.ndim == 2 and arr.shape[0] < arr.shape[1]:
        arr = arr.T  # (N, 6)

    for row in arr:
        if len(row) < 5:
            continue
        x_ctr, y_ctr, w, h = row[0], row[1], row[2], row[3]
        angle = row[4] if len(row) > 4 else 0.0
        raw_conf = row[5] if len(row) > 5 else row[4]
        conf = 1.0 / (1.0 + math.exp(-float(raw_conf)))  # sigmoid
        if conf < conf_threshold:
            continue

        img_x = col_off + x_ctr
        img_y = row_off + y_ctr

        geo_lon, geo_lat = _pixel_to_geo(img_x, img_y, affine)

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

        detections.append(
            {
                "detection_id": str(uuid.uuid4()),
                "geo_lat": geo_lat,
                "geo_lon": geo_lon,
                "confidence": float(conf),
                "obb_corners": geo_corners,
                "vessel_length_m": float(max(w, h)) * abs(affine.a),
            }
        )
    return detections


def _load_dual_band(scene_dir: Path) -> tuple[Any, Any]:
    """Load VH_dB + VV_dB from a scene directory, return (img_3ch, affine).

    Composes a 3-channel (H, W, 3) uint8 image matching training preprocess:
    channel 0 = VH normalised, channel 1 = VV normalised, channel 2 = VH-VV ratio.
    """
    import numpy as np
    import rasterio  # type: ignore[import-untyped]

    vh_path = scene_dir / "VH_dB.tif"
    vv_path = scene_dir / "VV_dB.tif"
    if not vh_path.exists():
        raise FileNotFoundError(f"Missing VH_dB.tif in {scene_dir}")

    with rasterio.open(vh_path) as src:
        vh = src.read(1).astype(np.float32)
        affine = src.transform

    if vv_path.exists():
        with rasterio.open(vv_path) as src:
            vv = src.read(1).astype(np.float32)
    else:
        vv = vh.copy()

    ratio = vh - vv

    def _norm(arr: np.ndarray) -> np.ndarray:
        mn, mx = np.nanmin(arr), np.nanmax(arr)
        if mx > mn:
            arr = (arr - mn) / (mx - mn) * 255
        else:
            arr = np.zeros_like(arr)
        return np.nan_to_num(arr, nan=0).astype(np.uint8)

    img = np.stack([_norm(vh), _norm(vv), _norm(ratio)], axis=-1)  # (H, W, 3)
    return img, affine


class YOLOInferenceNode(NodeBase):
    """Load VH+VV GeoTIFFs, tile into patches, run YOLO OBB inference, geo-transform."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            import numpy as np

            tile = state.current_tile  # type: ignore[attr-defined]

            file_path = tile.file_path
            if not file_path:
                return {"raw_detections": [], "pipeline_phase": "detection",
                        "last_error": "YOLOInferenceNode: no file_path"}

            scene_dir = Path(file_path)  # noqa: ASYNC240
            if scene_dir.is_file():
                scene_dir = scene_dir.parent
            if not scene_dir.exists() or not (scene_dir / "VH_dB.tif").exists():
                logger.warning("Scene dir missing VH_dB.tif: %s", scene_dir)
                return {"raw_detections": [], "pipeline_phase": "detection",
                        "last_error": f"YOLOInferenceNode: no VH_dB.tif in {scene_dir}"}

            img, affine = await asyncio.to_thread(_load_dual_band, scene_dir)

            patches = _tile_image(img, _PATCH_SIZE, _OVERLAP_FRAC)
            logger.info("Scene %s: %dx%d → %d patches", tile.tile_id, img.shape[1], img.shape[0], len(patches))

            model_path = os.environ.get("SDW_MODEL_PATH", "")
            if not model_path:
                data_dir = Path(__file__).resolve().parent.parent / "data" / "models"
                model_path = str(data_dir / "yolo11n-obb.pt")
            if not Path(model_path).exists():
                return {"raw_detections": [], "pipeline_phase": "detection",
                        "last_error": f"YOLOInferenceNode: model not found at {model_path}"}

            from ultralytics import YOLO
            model = await asyncio.to_thread(YOLO, model_path)
            logger.info("Loaded YOLO model: %s", model_path)

            conf_threshold = float(os.environ.get("SDW_CONF_THRESHOLD", "0.1"))
            all_detections: list[dict[str, Any]] = []

            for patch, r_off, c_off in patches:
                results = await asyncio.to_thread(
                    model.predict, patch, conf=conf_threshold, verbose=False,
                )
                if not results or not results[0].obb:
                    continue
                obb = results[0].obb
                for i in range(len(obb.conf)):
                    conf = float(obb.conf[i])
                    cls_id = int(obb.cls[i])
                    xywhr = obb.xywhr[i].cpu().numpy()
                    x_ctr, y_ctr, w, h, angle = xywhr

                    img_x = c_off + float(x_ctr)
                    img_y = r_off + float(y_ctr)
                    geo_lon, geo_lat = _pixel_to_geo(img_x, img_y, affine)

                    cos_a, sin_a = math.cos(float(angle)), math.sin(float(angle))
                    half_w, half_h = float(w) / 2, float(h) / 2
                    geo_corners: list[list[float]] = []
                    for dx, dy in [(-half_w, -half_h), (half_w, -half_h),
                                   (half_w, half_h), (-half_w, half_h)]:
                        rx = cos_a * dx - sin_a * dy
                        ry = sin_a * dx + cos_a * dy
                        gx, gy = _pixel_to_geo(img_x + rx, img_y + ry, affine)
                        geo_corners.append([gx, gy])

                    all_detections.append({
                        "detection_id": str(uuid.uuid4()),
                        "geo_lat": geo_lat,
                        "geo_lon": geo_lon,
                        "confidence": conf,
                        "obb_corners": geo_corners,
                        "vessel_length_m": float(max(w, h)) * abs(affine.a),
                        "tile_id": tile.tile_id,
                        "class_id": cls_id,
                        "class_name": model.names.get(cls_id, "unknown"),
                    })

            from demos.sentinel_dark_watch.graph.state import Detection

            detections = [Detection(**d) for d in all_detections]
            logger.info("YOLOInferenceNode: %d detections from %d patches", len(detections), len(patches))

            return {"raw_detections": detections, "pipeline_phase": "detection"}
        except Exception as exc:
            logger.exception("YOLOInferenceNode failed: %s", exc)
            return {"raw_detections": [], "pipeline_phase": "detection",
                    "last_error": f"YOLOInferenceNode: {exc}"}


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
            cur_side = (edge_e[0] - edge_s[0]) * (cur[1] - edge_s[1]) - (edge_e[1] - edge_s[1]) * (
                cur[0] - edge_s[0]
            )
            nxt_side = (edge_e[0] - edge_s[0]) * (nxt[1] - edge_s[1]) - (edge_e[1] - edge_s[1]) * (
                nxt[0] - edge_s[0]
            )
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
    closed = [*output, output[0]]
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
        try:
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
        except Exception as exc:
            logger.exception("NMSDeduplicationNode failed: %s", exc)
            # Pass through raw detections unfiltered on failure
            raw_fallback = list(getattr(state, "raw_detections", []))
            return {
                "detections": raw_fallback,
                "detection_count": len(raw_fallback),
                "last_error": f"NMSDeduplicationNode: {exc}",
                "pipeline_phase": "nms",
            }


# ---------------------------------------------------------------------------
# Land-Mask Filter
# ---------------------------------------------------------------------------


class LandMaskFilterNode(NodeBase):
    """Filter out detections whose centroid falls on land.

    Uses :func:`demos.sentinel_dark_watch.geo.point_on_land`.  If PostGIS
    is unreachable the filter is skipped (all detections kept) with a
    warning — we never discard valid detections due to infra failure.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            from demos.sentinel_dark_watch.geo import point_on_land

            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            if not detections:
                return {"detections": [], "detection_count": 0, "pipeline_phase": "land_filter"}

            try:
                import asyncpg

                conn = await asyncpg.connect(get_pg_dsn())
            except Exception:
                logger.warning(
                    "PostGIS unavailable — skipping land-mask filter, keeping all detections",
                )
                return {
                    "last_error": "LandMaskFilterNode: PostGIS unavailable",
                    "pipeline_phase": "land_filter",
                }

            try:
                water_detections: list[Any] = []
                for det in detections:
                    try:
                        on_land = await point_on_land(conn, det.geo_lat, det.geo_lon)
                        if not on_land:
                            water_detections.append(det)
                    except Exception:
                        # On per-detection query failure, keep the detection
                        logger.warning(
                            "Land-mask query failed for detection %s — keeping",
                            det.detection_id,
                        )
                        water_detections.append(det)
            finally:
                await conn.close()

            return {
                "detections": water_detections,
                "detection_count": len(water_detections),
                "pipeline_phase": "land_filter",
            }
        except Exception as exc:
            logger.exception("LandMaskFilterNode failed: %s", exc)
            return {"last_error": f"LandMaskFilterNode: {exc}", "pipeline_phase": "land_filter"}


# ---------------------------------------------------------------------------
# AIS Correlation — predicted-position matching
# ---------------------------------------------------------------------------


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    earth_r = 6_371_000.0  # Earth radius in metres
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return earth_r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class AISCorrelationNode(NodeBase):
    """Match SAR detections to AIS positions using predicted-position matching.

    For each AIS report within the tile bounding box + time window, compute
    predicted position at SAR acquisition time:
        predicted_lat = lat + speed_kn * cos(heading_rad) * dt_hours / 60
        predicted_lon = lon + speed_kn * sin(heading_rad) * dt_hours / 60

    Match to nearest detection within ``ais_match_radius_m``.
    Unmatched detections → ``dark_vessel=True``.
    Matched detections → enriched with MMSI, name, flag_state, vessel_type.

    If query fails → all detections marked ``dark_vessel=True`` (conservative).
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            if not detections:
                return {"detections": [], "pipeline_phase": "ais_correlation"}

            tile = state.current_tile  # type: ignore[attr-defined]
            time_window_min: int = state.ais_query_time_window_min  # type: ignore[attr-defined]
            match_radius_m: int = state.ais_match_radius_m  # type: ignore[attr-defined]

            # Compute bounding box from detections
            lats = [d.geo_lat for d in detections]
            lons = [d.geo_lon for d in detections]
            margin = 0.1  # ~11km padding
            min_lat, max_lat = min(lats) - margin, max(lats) + margin
            min_lon, max_lon = min(lons) - margin, max(lons) + margin

            # SAR acquisition timestamp
            acq_ts = tile.timestamp if tile.timestamp else ""

            # Try Nautilus broker first, fall back to direct PostGIS
            rows: list[Any] = []
            broker_used = False
            try:
                from harbor.serve.contextvars import current_broker
                broker = current_broker()
                response = await broker.arequest(
                    agent_id="sdw-pipeline",
                    intent="ais-correlation",
                    context={
                        "min_lat": min_lat, "max_lat": max_lat,
                        "min_lon": min_lon, "max_lon": max_lon,
                        "acq_ts": acq_ts,
                        "time_window_min": time_window_min,
                    },
                )
                ais_rows = response.data.get("ais_buffer", [])
                rows = ais_rows
                broker_used = True
                logger.info("AIS via Nautilus broker: %d rows from %s",
                            len(rows), response.sources_queried)
            except Exception as broker_exc:
                logger.info("Nautilus broker unavailable (%s), falling back to direct PostGIS",
                            type(broker_exc).__name__)

            if not broker_used:
                try:
                    import asyncpg
                    conn = await asyncpg.connect(get_pg_dsn())
                except Exception:
                    logger.warning("PostGIS unavailable — marking all as dark vessels")
                    for det in detections:
                        det.dark_vessel = True
                    return {
                        "detections": detections,
                        "last_error": "AISCorrelationNode: DB unavailable",
                        "pipeline_phase": "ais_correlation",
                    }
                try:
                    if acq_ts:
                        rows = await conn.fetch(
                            "SELECT mmsi, ship_name, flag_state, vessel_type,"
                            "       lat, lon, speed_kn, heading, recorded_at"
                            "  FROM ais_positions"
                            " WHERE lat BETWEEN $1 AND $2"
                            "   AND lon BETWEEN $3 AND $4"
                            "   AND recorded_at BETWEEN $5::timestamptz - ($6 || ' minutes')::interval"
                            "                          AND $5::timestamptz + ($6 || ' minutes')::interval",
                            min_lat, max_lat, min_lon, max_lon, acq_ts, str(time_window_min),
                        )
                    else:
                        rows = await conn.fetch(
                            "SELECT mmsi, ship_name, flag_state, vessel_type,"
                            "       lat, lon, speed_kn, heading, recorded_at"
                            "  FROM ais_positions"
                            " WHERE lat BETWEEN $1 AND $2"
                            "   AND lon BETWEEN $3 AND $4",
                            min_lat, max_lat, min_lon, max_lon,
                        )
                except Exception:
                    logger.warning("AIS query failed — marking all as dark vessels")
                    for det in detections:
                        det.dark_vessel = True
                    return {
                        "detections": detections,
                        "last_error": "AISCorrelationNode: query failed",
                        "pipeline_phase": "ais_correlation",
                    }
                finally:
                    await conn.close()

            # Compute predicted positions for each AIS report
            from demos.sentinel_dark_watch.geo import predicted_ais_position

            ais_predicted: list[dict[str, Any]] = []
            for row in rows:
                speed_kn = float(row["speed_kn"]) if row["speed_kn"] else 0.0
                heading_deg = float(row["heading"]) if row["heading"] else 0.0

                # Time delta in hours
                dt_hours = 0.0
                if acq_ts and row["recorded_at"]:
                    try:
                        from datetime import datetime

                        ais_time = row["recorded_at"]
                        if isinstance(ais_time, str):
                            ais_time = datetime.fromisoformat(ais_time)
                        if isinstance(acq_ts, str):
                            acq_dt = datetime.fromisoformat(acq_ts)
                        else:
                            acq_dt = acq_ts
                        # Ensure timezone-aware
                        if ais_time.tzinfo is None:
                            ais_time = ais_time.replace(tzinfo=UTC)
                        if acq_dt.tzinfo is None:
                            acq_dt = acq_dt.replace(tzinfo=UTC)
                        dt_hours = (acq_dt - ais_time).total_seconds() / 3600.0
                    except Exception:
                        dt_hours = 0.0

                lat = float(row["lat"])
                lon = float(row["lon"])
                pred_lat, pred_lon = predicted_ais_position(
                    lat,
                    lon,
                    speed_kn,
                    heading_deg,
                    dt_hours,
                )

                ais_predicted.append(
                    {
                        "mmsi": str(row["mmsi"]),
                        "ship_name": row["ship_name"] or "",
                        "flag_state": row["flag_state"] or "",
                        "vessel_type": row["vessel_type"] or "",
                        "predicted_lat": pred_lat,
                        "predicted_lon": pred_lon,
                    }
                )

            # Match detections to AIS by minimum distance within radius
            matched_det_indices: set[int] = set()
            matched_ais_indices: set[int] = set()

            # Build distance matrix and greedily match closest pairs
            pairs: list[tuple[float, int, int]] = []
            for di, det in enumerate(detections):
                for ai, ais in enumerate(ais_predicted):
                    dist = _haversine_m(
                        det.geo_lat,
                        det.geo_lon,
                        ais["predicted_lat"],
                        ais["predicted_lon"],
                    )
                    if dist <= match_radius_m:
                        pairs.append((dist, di, ai))

            pairs.sort(key=lambda x: x[0])
            for _dist, di, ai in pairs:
                if di in matched_det_indices or ai in matched_ais_indices:
                    continue
                matched_det_indices.add(di)
                matched_ais_indices.add(ai)
                det = detections[di]
                ais = ais_predicted[ai]
                det.dark_vessel = False
                det.ais_mmsi = ais["mmsi"]
                det.ais_vessel_name = ais["ship_name"]
                det.ais_flag_state = ais["flag_state"]
                det.ais_vessel_type = ais["vessel_type"]

            # Unmatched detections → dark vessel
            for di, det in enumerate(detections):
                if di not in matched_det_indices:
                    det.dark_vessel = True

            return {"detections": detections, "pipeline_phase": "ais_correlation"}
        except Exception as exc:
            logger.exception("AISCorrelationNode failed: %s", exc)
            # Conservative: mark all as dark vessels
            fallback_dets = list(getattr(state, "detections", []))
            for det in fallback_dets:
                det.dark_vessel = True
            return {
                "detections": fallback_dets,
                "last_error": f"AISCorrelationNode: {exc}",
                "pipeline_phase": "ais_correlation",
            }


# ---------------------------------------------------------------------------
# Geo-Context Enrichment — PostGIS + DSPy synthesis
# ---------------------------------------------------------------------------


class GeoContextNode(NodeBase):
    """Enrich detections with EEZ, port/coast distance via PostGIS.

    Step 1: Spatial queries — ``ST_Contains`` for EEZ point-in-polygon,
    ``ST_Distance`` for nearest port and coastline distances.
    Step 2: DSPy ``ChainOfThought`` synthesis for a human-readable
    ``geo_summary``.  If DSPy or the LLM is unavailable, a templated
    fallback populates the field instead.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            from demos.sentinel_dark_watch.geo import (
                nearest_coast_distance_m,
                nearest_port,
                point_in_eez,
            )
            from demos.sentinel_dark_watch.graph.signatures import (
                DSPY_AVAILABLE,
                FALLBACK_TEMPLATES,
            )

            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            if not detections:
                return {"detections": [], "pipeline_phase": "geo_context"}

            # Step 1 — PostGIS enrichment
            conn = None
            try:
                import asyncpg

                conn = await asyncpg.connect(get_pg_dsn())
            except Exception:
                logger.warning("PostGIS unavailable — skipping geo-context enrichment")

            for det in detections:
                if conn is not None:
                    try:
                        eez_name = await point_in_eez(conn, det.geo_lat, det.geo_lon)
                        if eez_name:
                            det.eez_name = eez_name

                        _port_name, port_dist_m = await nearest_port(conn, det.geo_lat, det.geo_lon)
                        if port_dist_m:
                            det.distance_to_port_nm = port_dist_m / 1852.0

                        coast_dist_m = await nearest_coast_distance_m(
                            conn,
                            det.geo_lat,
                            det.geo_lon,
                        )
                        if coast_dist_m is not None:
                            det.distance_to_coast_nm = coast_dist_m / 1852.0

                    except Exception:
                        logger.warning(
                            "Geo query failed for detection %s — using defaults",
                            det.detection_id,
                        )

            if conn is not None:
                await conn.close()

            # Step 2 — DSPy ChainOfThought synthesis for geo_summary
            cot = None
            if DSPY_AVAILABLE:
                try:
                    import dspy

                    from demos.sentinel_dark_watch.graph.signatures import GeoContextSignature

                    cot = dspy.ChainOfThought(GeoContextSignature)
                except Exception:
                    logger.info("DSPy available but ChainOfThought init failed — using fallback")

            for det in detections:
                ais_status = (
                    "dark (no AIS)"
                    if det.dark_vessel
                    else f"AIS matched ({det.ais_mmsi or 'unknown'})"
                )

                if cot is not None:
                    try:
                        result = cot(
                            detection_lat=det.geo_lat,
                            detection_lon=det.geo_lon,
                            dark_vessel=det.dark_vessel,
                            eez_name=det.eez_name or "Unknown",
                            distance_to_port_nm=det.distance_to_port_nm,
                            nearest_port_name="",  # populated by PostGIS if available
                            distance_to_coast_nm=det.distance_to_coast_nm,
                            ais_status=ais_status,
                        )
                        det.geo_summary = result.geo_summary
                        continue
                    except Exception:
                        logger.warning("LLM unavailable for geo-context — using templated fallback")

                # Templated fallback
                det.geo_summary = FALLBACK_TEMPLATES["geo_context"].format(
                    lat=det.geo_lat,
                    lon=det.geo_lon,
                    eez_name=det.eez_name or "Unknown EEZ",
                    distance_to_port_nm=det.distance_to_port_nm,
                    ais_status=ais_status,
                )

            return {"detections": detections, "pipeline_phase": "geo_context"}
        except Exception as exc:
            logger.exception("GeoContextNode failed: %s", exc)
            return {"last_error": f"GeoContextNode: {exc}", "pipeline_phase": "geo_context"}


# ---------------------------------------------------------------------------
# Risk Scoring — configurable weights + risk levels
# ---------------------------------------------------------------------------

# Sensitive EEZs default; overridable via SENSITIVE_EEZS env var (comma-separated)
_DEFAULT_SENSITIVE_EEZS = {"Iranian", "North Korean", "Syrian", "Venezuelan"}


def _load_sensitive_eezs() -> set[str]:
    raw = os.environ.get("SENSITIVE_EEZS", "")
    if raw.strip():
        return {s.strip() for s in raw.split(",") if s.strip()}
    return set(_DEFAULT_SENSITIVE_EEZS)


def _env_int(name: str, fallback: int) -> int:
    """Read an integer from env, falling back to *fallback*."""
    raw = os.environ.get(name, "")
    if raw.strip():
        try:
            return int(raw)
        except ValueError:
            pass
    return fallback


def _env_float(name: str, fallback: float) -> float:
    """Read a float from env, falling back to *fallback*."""
    raw = os.environ.get(name, "")
    if raw.strip():
        try:
            return float(raw)
        except ValueError:
            pass
    return fallback


class RiskScoringNode(NodeBase):
    """Apply configurable risk scoring formula to each detection.

    Scoring weights come from state fields (``risk_weight_*``),
    with env-var overrides (AC-6.4): ``RISK_WEIGHT_DARK_VESSEL``,
    ``RISK_WEIGHT_SENSITIVE_EEZ``, ``RISK_WEIGHT_FAR_FROM_PORT``,
    ``RISK_WEIGHT_LARGE_VESSEL``, ``RISK_WEIGHT_CONFIDENCE_MAX``.

    Thresholds overridable via ``FAILURE_THRESHOLD``,
    ``LOW_CONF_THRESHOLD``, ``AIS_MATCH_RADIUS_M``.

    Risk levels:
        Critical  80-100
        High      60-79
        Medium    40-59
        Low        0-39
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            from demos.sentinel_dark_watch.graph.state import RiskLevel

            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            if not detections:
                return {"detections": [], "pipeline_phase": "risk_scoring"}

            sensitive_eezs = _load_sensitive_eezs()

            # Weights: env var overrides state field defaults
            s = state  # type: ignore[attr-defined]
            w_dark = _env_int("RISK_WEIGHT_DARK_VESSEL", s.risk_weight_dark_vessel)
            w_eez = _env_int("RISK_WEIGHT_SENSITIVE_EEZ", s.risk_weight_sensitive_eez)
            w_port = _env_int("RISK_WEIGHT_FAR_FROM_PORT", s.risk_weight_far_from_port)
            w_vessel = _env_int("RISK_WEIGHT_LARGE_VESSEL", s.risk_weight_large_vessel)
            w_conf_max = _env_int("RISK_WEIGHT_CONFIDENCE_MAX", s.risk_weight_confidence_max)
            low_conf_threshold: float = _env_float(
                "LOW_CONF_THRESHOLD",
                s.low_conf_threshold,
            )

            has_low_conf = False

            for det in detections:
                score = 0
                if det.dark_vessel:
                    score += w_dark
                if det.eez_name in sensitive_eezs:
                    score += w_eez
                if det.distance_to_port_nm > 50:
                    score += w_port
                if det.vessel_length_m > 100:
                    score += w_vessel
                score += int(det.confidence * w_conf_max)
                score = min(score, 100)

                det.risk_score = score

                if score >= 80:
                    det.risk_level = RiskLevel.CRITICAL
                elif score >= 60:
                    det.risk_level = RiskLevel.HIGH
                elif score >= 40:
                    det.risk_level = RiskLevel.MEDIUM
                else:
                    det.risk_level = RiskLevel.LOW

                if det.confidence < low_conf_threshold:
                    has_low_conf = True

            return {
                "detections": detections,
                "has_low_confidence_detections": has_low_conf,
                "pipeline_phase": "risk_scoring",
            }
        except Exception as exc:
            logger.exception("RiskScoringNode failed: %s", exc)
            return {"last_error": f"RiskScoringNode: {exc}", "pipeline_phase": "risk_scoring"}


# ---------------------------------------------------------------------------
# Reporting — DSPy narrative synthesis with templated fallback
# ---------------------------------------------------------------------------


class ReportingNode(NodeBase):
    """Assemble structured report sections and synthesize narrative.

    Sections: Detection Summary, Imagery Reference, AIS Correlation,
    Geo-Context, Risk Assessment, Recommended Actions.

    Uses DSPy ``ChainOfThought`` for narrative synthesis when available.
    Falls back to a templated report if DSPy or the LLM is unavailable.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            from demos.sentinel_dark_watch.graph.signatures import (
                DSPY_AVAILABLE,
                FALLBACK_TEMPLATES,
            )

            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            if not detections:
                return {"detections": [], "pipeline_phase": "reporting"}

            tile = state.current_tile  # type: ignore[attr-defined]

            dark_count = sum(1 for d in detections if d.dark_vessel)
            ais_matched = len(detections) - dark_count

            # Determine highest risk for overall summary
            risk_scores = [d.risk_score for d in detections]
            max_risk_score = max(risk_scores) if risk_scores else 0
            max_risk_det = max(detections, key=lambda d: d.risk_score)
            overall_risk_level = str(max_risk_det.risk_level).upper() if detections else "LOW"

            # Aggregate geo summaries
            geo_parts = [d.geo_summary for d in detections if d.geo_summary]
            combined_geo = " ".join(geo_parts) if geo_parts else "No geo-context available."

            # Recommended actions based on risk
            actions_list: list[str] = []
            if max_risk_score >= 80:
                actions_list.append(
                    "- IMMEDIATE: Alert maritime authorities and initiate tracking.",
                )
            if dark_count > 0:
                actions_list.append("- Flag dark vessel(s) for enhanced monitoring.")
            if any(d.distance_to_port_nm > 50 for d in detections):
                actions_list.append("- Monitor vessels operating far from port.")
            if any(d.confidence < 0.5 for d in detections):
                actions_list.append("- Review low-confidence detections manually.")
            if not actions_list:
                actions_list.append("- Continue routine monitoring.")
            actions_text = "\n".join(actions_list)

            # Try DSPy narrative synthesis
            cot = None
            if DSPY_AVAILABLE:
                try:
                    import dspy

                    from demos.sentinel_dark_watch.graph.signatures import ReportingSignature

                    cot = dspy.ChainOfThought(ReportingSignature)
                except Exception:
                    logger.info("DSPy available but ChainOfThought init failed — using fallback")

            report_text = ""
            if cot is not None:
                try:
                    result = cot(
                        detection_count=len(detections),
                        dark_vessel_count=dark_count,
                        ais_matched_count=ais_matched,
                        overall_risk_level=overall_risk_level,
                        max_risk_score=max_risk_score,
                        geo_summary=combined_geo,
                        tile_id=tile.tile_id,
                        recommended_actions=actions_text,
                    )
                    report_text = result.report
                except Exception:
                    logger.warning("LLM unavailable for reporting — using templated fallback")

            if not report_text:
                report_text = FALLBACK_TEMPLATES["reporting"].format(
                    detection_count=len(detections),
                    tile_id=tile.tile_id,
                    scene_id=tile.scene_id,
                    dark_count=dark_count,
                    ais_matched=ais_matched,
                    geo_summary=combined_geo,
                    risk_level=overall_risk_level,
                    risk_score=max_risk_score,
                    actions=actions_text,
                )

            # Assign report to each detection
            for det in detections:
                det.report_text = report_text

            return {"detections": detections, "pipeline_phase": "reporting"}
        except Exception as exc:
            logger.exception("ReportingNode failed: %s", exc)
            return {"last_error": f"ReportingNode: {exc}", "pipeline_phase": "reporting"}


# ---------------------------------------------------------------------------
# SAR Chip Extraction — crop 128x128 around detection centroid
# ---------------------------------------------------------------------------


class EmitSARChipsNode(NodeBase):
    """Crop 128x128 SAR chip around each detection centroid.

    Saves chips as PNG via rasterio/Pillow.  If geo dependencies are
    missing the chip step is silently skipped per-detection (POC
    graceful-degrade).  A failure on one detection does not block
    subsequent detections.
    """

    _CHIP_SIZE = 128

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            if not detections:
                return {"detections": [], "pipeline_phase": "emit_chips"}

            tile = state.current_tile  # type: ignore[attr-defined]
            file_path = tile.file_path if tile else ""

            # Guard: rasterio + Pillow may not be installed
            try:
                import numpy as np
                import rasterio
            except ImportError:
                logger.warning("rasterio/numpy not installed — skipping SAR chip extraction")
                return {"detections": detections, "pipeline_phase": "emit_chips"}

            try:
                from PIL import Image
            except ImportError:
                logger.warning("Pillow not installed — skipping SAR chip extraction")
                return {"detections": detections, "pipeline_phase": "emit_chips"}

            if not file_path or not Path(file_path).exists():  # noqa: ASYNC240
                logger.warning("Tile file not found: %s — skipping chip extraction", file_path)
                return {"detections": detections, "pipeline_phase": "emit_chips"}

            tif_path = Path(file_path)
            if tif_path.is_dir():
                tif_path = tif_path / "VH_dB.tif"
            if not tif_path.exists():
                logger.warning("No GeoTIFF at %s — skipping chips", tif_path)
                return {"detections": detections, "pipeline_phase": "emit_chips"}

            # Open source GeoTIFF once, crop per-detection
            try:
                src = rasterio.open(str(tif_path))
            except Exception:
                logger.warning("Failed to open GeoTIFF %s — skipping chips", file_path)
                return {"detections": detections, "pipeline_phase": "emit_chips"}

            try:
                img = src.read()  # (C, H, W)
                transform = src.transform
                _, h, w = img.shape

                # Ensure (H, W, C) for Pillow
                img_hwc = img.transpose(1, 2, 0) if img.ndim == 3 else img

                half = self._CHIP_SIZE // 2
                chip_dir = Path(file_path).parent / "chips"
                chip_dir.mkdir(parents=True, exist_ok=True)

                for det in detections:
                    try:
                        # Geo-coords → pixel coords via inverse affine
                        inv = ~transform
                        px_x, px_y = inv * (det.geo_lon, det.geo_lat)
                        px_x, px_y = round(px_x), round(px_y)

                        # Crop bounds (clamped to image)
                        r0 = max(0, px_y - half)
                        r1 = min(h, px_y + half)
                        c0 = max(0, px_x - half)
                        c1 = min(w, px_x + half)

                        if r1 - r0 < 2 or c1 - c0 < 2:
                            logger.warning(
                                "Chip too small for detection %s — skipping",
                                det.detection_id,
                            )
                            continue

                        chip_arr = img_hwc[r0:r1, c0:c1]

                        # Normalize to uint8 for PNG
                        if chip_arr.dtype != np.uint8:
                            cmin, cmax = chip_arr.min(), chip_arr.max()
                            if cmax > cmin:
                                normed = (chip_arr - cmin) / (cmax - cmin) * 255
                                chip_arr = normed.astype(np.uint8)
                            else:
                                chip_arr = np.zeros_like(chip_arr, dtype=np.uint8)

                        # Save as PNG
                        if chip_arr.ndim == 3 and chip_arr.shape[2] == 1:
                            chip_img = Image.fromarray(chip_arr[:, :, 0], mode="L")
                        elif chip_arr.ndim == 2:
                            chip_img = Image.fromarray(chip_arr, mode="L")
                        else:
                            chip_img = Image.fromarray(chip_arr)

                        chip_path = chip_dir / f"{det.detection_id}.png"
                        chip_img.save(str(chip_path))
                        det.chip_artifact_ref = str(chip_path)

                    except Exception:
                        logger.warning(
                            "Failed to extract chip for detection %s — continuing",
                            det.detection_id,
                        )
                        continue
            finally:
                src.close()

            return {"detections": detections, "pipeline_phase": "emit_chips"}
        except Exception as exc:
            logger.exception("EmitSARChipsNode failed: %s", exc)
            return {"last_error": f"EmitSARChipsNode: {exc}", "pipeline_phase": "emit_chips"}


# ---------------------------------------------------------------------------
# Analyst Review — HITL interrupt gate
# ---------------------------------------------------------------------------


class AnalystReviewNode(NodeBase):
    """HITL interrupt gate — pause for analyst review.

    First dispatch: raises :class:`_HitInterrupt` with a prompt listing
    detection count and tile ID.  The loop transitions to
    ``awaiting-input`` and emits a :class:`WaitingForInputEvent`.

    On resume (after ``POST /runs/{id}/respond``): the response payload
    has been asserted as a Fathom fact.  A separate passthrough node
    (``branch_resp_review``) routes the graph forward.  Corrections
    from the analyst response are written to the Postgres ``corrections``
    table.

    Matches the :class:`harbor.nodes.interrupt.InterruptNode` pattern
    from CVE-rem exactly: raise ``_HitInterrupt(InterruptAction(...))``.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            # Check if this is a resume (response already populated)
            response_decision = getattr(state, "response_decision", None)
            if response_decision:
                corrections = getattr(state, "analyst_corrections", []) or []
                if corrections:
                    await self._write_corrections(corrections, state)
                return {"pipeline_phase": "analyst_review"}

            # Skip interrupt when no low-confidence detections need review
            has_low_conf = getattr(state, "has_low_confidence_detections", False)
            if not has_low_conf:
                return {"pipeline_phase": "analyst_review"}

            # First dispatch: build prompt and raise interrupt
            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            tile_id = getattr(state, "current_tile_id", "unknown")
            detection_count = len(detections)
            dark_count = sum(1 for d in detections if getattr(d, "dark_vessel", False))

            prompt = (
                f"Analyst review required: {detection_count} detection(s) "
                f"({dark_count} dark vessel(s)) from tile {tile_id}.\n"
                f"Please review detections and provide corrections.\n"
                f"Actions: confirm, reject (false positive), flag_for_review, override_risk."
            )

            payload = {
                "tile_id": tile_id,
                "detection_count": detection_count,
                "dark_vessel_count": dark_count,
                "detection_ids": [d.detection_id for d in detections],
            }

            from harbor.graph.loop import _HitInterrupt  # pyright: ignore[reportPrivateUsage]
            from harbor.ir._models import InterruptAction

            action = InterruptAction(
                prompt=prompt,
                interrupt_payload=payload,
                requested_capability="sdw.analyst_review",
                timeout=None,
                on_timeout="halt",
            )
            raise _HitInterrupt(action)
        except Exception as exc:
            # Re-raise _HitInterrupt — it's not an error
            from harbor.graph.loop import _HitInterrupt  # pyright: ignore[reportPrivateUsage]

            if isinstance(exc, _HitInterrupt):
                raise
            logger.exception("AnalystReviewNode failed: %s", exc)
            return {"last_error": f"AnalystReviewNode: {exc}", "pipeline_phase": "analyst_review"}

    async def _write_corrections(self, corrections: list[Any], state: BaseModel) -> None:
        """Persist analyst corrections to the Postgres ``corrections`` table."""
        try:
            import asyncpg

            conn = await asyncpg.connect(get_pg_dsn())
        except Exception:
            logger.warning("PostGIS unavailable — cannot persist analyst corrections")
            return

        try:
            run_id = str(getattr(state, "run_id", ""))
            for corr in corrections:
                _is_dict = isinstance(corr, dict)
                detection_id = (
                    corr.get("detection_id", "") if _is_dict else getattr(corr, "detection_id", "")
                )
                decision = corr.get("decision", "") if _is_dict else getattr(corr, "decision", "")
                note = corr.get("note", "") if _is_dict else getattr(corr, "note", "")

                await conn.execute(
                    "INSERT INTO corrections (detection_id, run_id, decision, note)"
                    " VALUES ($1, $2, $3, $4)"
                    " ON CONFLICT DO NOTHING",
                    detection_id,
                    run_id,
                    decision,
                    note,
                )
        except Exception:
            logger.warning("Failed to write corrections to Postgres")
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# Metrics Collector — run-level metrics
# ---------------------------------------------------------------------------


class MetricsCollectorNode(NodeBase):
    """Compute and persist run-level metrics.

    Aggregates detection counts, dark vessels, AIS matches, false
    positives (from ``corrections`` where ``decision='reject'``), and
    processing time (``now - run_started_at``).  Writes to the Postgres
    ``run_metrics`` table via asyncpg.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            from datetime import datetime

            detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
            detection_count = len(detections)
            dark_vessel_count = sum(1 for d in detections if getattr(d, "dark_vessel", False))
            ais_match_count = detection_count - dark_vessel_count

            # False positives from corrections
            corrections = getattr(state, "analyst_corrections", []) or []
            false_positive_count = 0
            for corr in corrections:
                _is_dict = isinstance(corr, dict)
                decision = corr.get("decision", "") if _is_dict else getattr(corr, "decision", "")
                if decision == "reject":
                    false_positive_count += 1

            # Processing time — compute from run_started_at
            run_started_at = getattr(state, "run_started_at", None)
            now = datetime.now(UTC)
            processing_secs = 0.0
            if run_started_at:
                if isinstance(run_started_at, str):
                    try:
                        run_started_at = datetime.fromisoformat(run_started_at)
                    except Exception:
                        run_started_at = None
                if run_started_at:
                    if run_started_at.tzinfo is None:
                        run_started_at = run_started_at.replace(tzinfo=UTC)
                    processing_secs = (now - run_started_at).total_seconds()

            # Also sum perf_marks for per-node breakdown
            perf_marks: dict[str, float] = getattr(state, "perf_marks", {}) or {}
            if perf_marks:
                logger.info(
                    "Per-node timing: %s  |  total pipeline: %.2fs",
                    ", ".join(f"{k}={v:.2f}s" for k, v in perf_marks.items()),
                    processing_secs,
                )
            else:
                logger.info("Total pipeline processing time: %.2fs", processing_secs)

            # Build RunMetrics
            from demos.sentinel_dark_watch.graph.state import RunMetrics

            metrics = RunMetrics(
                total_detections=detection_count,
                dark_vessels_flagged=dark_vessel_count,
                ais_matched=ais_match_count,
                false_positives_rejected=false_positive_count,
                processing_time_seconds=processing_secs,
            )

            # Write to Postgres
            run_id = str(getattr(state, "run_id", ""))
            await self._write_metrics(run_id, metrics)

            return {
                "run_metrics": metrics,
                "pipeline_phase": "metrics",
            }
        except Exception as exc:
            logger.exception("MetricsCollectorNode failed: %s", exc)
            return {"last_error": f"MetricsCollectorNode: {exc}", "pipeline_phase": "metrics"}

    async def _write_metrics(self, run_id: str, metrics: Any) -> None:
        """Persist run metrics to the ``run_metrics`` table."""
        try:
            import asyncpg

            conn = await asyncpg.connect(get_pg_dsn())
        except Exception:
            logger.warning("PostGIS unavailable — cannot persist run metrics")
            return

        try:
            await conn.execute(
                "INSERT INTO run_metrics"
                " (run_id, total_detections, dark_vessels,"
                "  ais_matched, false_positives, processing_secs)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                run_id,
                metrics.total_detections,
                metrics.dark_vessels_flagged,
                metrics.ais_matched,
                metrics.false_positives_rejected,
                metrics.processing_time_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to write run metrics to Postgres: %s", exc)
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# Retrain Trigger — correction-threshold gate
# ---------------------------------------------------------------------------

_RETRAIN_CORRECTION_THRESHOLD = 10


class RetrainTriggerNode(NodeBase):
    """Check if accumulated corrections exceed the retrain threshold.

    If ``corrections_count >= 10``: log that a retrain sub-graph dispatch
    would be triggered (POC — actual :class:`SubGraphNode` dispatch is
    deferred to Phase 2).

    Otherwise: proceed (return empty dict so the graph continues to
    ``action_done``).
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            corrections_count: int = getattr(state, "corrections_count", 0) or 0

            if corrections_count >= _RETRAIN_CORRECTION_THRESHOLD:
                logger.info(
                    "Retrain threshold reached (%d >= %d) — "
                    "retrain sub-graph dispatch would be triggered (POC deferred)",
                    corrections_count,
                    _RETRAIN_CORRECTION_THRESHOLD,
                )
                return {
                    "retrain_triggered": True,
                    "pipeline_phase": "retrain_trigger",
                }

            logger.info(
                "Corrections below retrain threshold (%d < %d) — skipping retrain",
                corrections_count,
                _RETRAIN_CORRECTION_THRESHOLD,
            )
            return {}
        except Exception as exc:
            logger.exception("RetrainTriggerNode failed: %s", exc)
            return {"last_error": f"RetrainTriggerNode: {exc}", "pipeline_phase": "retrain_trigger"}


# ---------------------------------------------------------------------------
# Retrain Sub-Graph — Collect Corrections
# ---------------------------------------------------------------------------


class RetrainCollectNode(NodeBase):
    """Query unconsumed corrections and prepare merged training data.

    Reads from the Postgres ``corrections`` table where ``consumed=false``,
    counts them, sets ``corrections_count`` and ``merged_training_samples``
    on the retrain state, then marks the rows as consumed.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            original_samples: int = getattr(state, "original_training_samples", 0)

            try:
                import asyncpg

                conn = await asyncpg.connect(get_pg_dsn())
            except Exception:
                logger.warning("PostGIS unavailable — cannot collect corrections")
                return {"corrections_count": 0, "merged_training_samples": original_samples}

            try:
                # Fetch unconsumed corrections
                rows = await conn.fetch(
                    "SELECT id FROM corrections WHERE consumed = false",
                )
                count = len(rows)

                if count > 0:
                    # Mark consumed
                    ids = [r["id"] for r in rows]
                    await conn.execute(
                        "UPDATE corrections SET consumed = true WHERE id = ANY($1::int[])",
                        ids,
                    )
                    logger.info("Collected %d unconsumed corrections", count)
                else:
                    logger.info("No unconsumed corrections found")

            except Exception:
                logger.warning("Failed to query/update corrections — returning zero")
                count = 0
            finally:
                await conn.close()

            return {
                "corrections_count": count,
                "merged_training_samples": original_samples + count,
            }
        except Exception as exc:
            logger.exception("RetrainCollectNode failed: %s", exc)
            return {"corrections_count": 0, "last_error": f"RetrainCollectNode: {exc}"}


# ---------------------------------------------------------------------------
# Retrain Sub-Graph — Train Model
# ---------------------------------------------------------------------------


class RetrainTrainNode(NodeBase):
    """Shell out to ``scripts/train_detector.py`` to fine-tune the YOLO model.

    Captures the new model path and mAP from stdout, then registers the
    new version in :class:`harbor.ml.registry.ModelRegistry`.

    If the train script is unavailable or fails, placeholder metrics are
    set so the rest of the retrain sub-graph can still evaluate
    champion vs. challenger.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            import json
            import subprocess

            merged_samples: int = getattr(state, "merged_training_samples", 0)

            # Locate train script relative to this module
            script = Path(__file__).resolve().parent.parent / "scripts" / "train_detector.py"  # noqa: ASYNC240

            if not script.exists():
                logger.warning(
                    "train_detector.py not found at %s — using placeholder metrics",
                    script,
                )
                return {
                    "challenger_version": "placeholder-v0",
                    "challenger_map50": 0.0,
                }

            # Shell out to training script
            cmd = [
                "python",
                str(script),
                "--data",
                "merged",
                "--epochs",
                "5",  # POC: minimal epochs
            ]

            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            except Exception as exc:
                logger.warning("train_detector.py execution failed: %s — using placeholder", exc)
                return {
                    "challenger_version": "placeholder-v0",
                    "challenger_map50": 0.0,
                    "last_error": f"RetrainTrainNode: {exc}",
                }

            # Parse output — expect JSON line with model_path and map50
            model_path = ""
            map50 = 0.0
            version = ""
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        model_path = data.get("model_path", model_path)
                        map50 = float(data.get("map50", map50))
                        version = data.get("version", version)
                    except json.JSONDecodeError:
                        continue

            if not version:
                version = f"retrain-{merged_samples}"

            # Register in ModelRegistry
            try:
                from harbor.ml.registry import ModelRegistry

                registry = ModelRegistry()
                await registry.register(
                    model_id="sdw-detector",
                    version=version,
                    file_uri=model_path,
                )
                logger.info("Registered challenger model %s (mAP=%.3f)", version, map50)
            except Exception:
                logger.warning("ModelRegistry unavailable — challenger registered in state only")

            return {
                "challenger_version": version,
                "challenger_map50": map50,
            }
        except Exception as exc:
            logger.exception("RetrainTrainNode failed: %s", exc)
            return {
                "challenger_version": "error",
                "challenger_map50": 0.0,
                "last_error": f"RetrainTrainNode: {exc}",
            }


# ---------------------------------------------------------------------------
# Retrain Sub-Graph — Champion / Challenger Comparison
# ---------------------------------------------------------------------------


class ChampionChallengerNode(NodeBase):
    """Compare champion (production alias) vs. challenger model on mAP.

    Loads both models' metadata from :class:`ModelRegistry`, compares
    ``map50``.  If the challenger wins, it is promoted to the
    ``production`` alias via ``registry.alias()``.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            challenger_version: str = getattr(state, "challenger_version", "")
            challenger_map50: float = getattr(state, "challenger_map50", 0.0)

            # Load champion info from ModelRegistry
            champion_version = ""
            champion_map50 = 0.0

            try:
                from harbor.ml.registry import ModelRegistry

                registry = ModelRegistry()
                entry = await registry.load_alias("sdw-detector", "production")
                champion_version = entry.version
                # mAP stored in registry metadata if available; fallback to state
                champion_map50 = getattr(entry, "map50", 0.0) or getattr(
                    state, "champion_map50", 0.0
                )
            except Exception:
                logger.warning(
                    "ModelRegistry unavailable — using state champion_map50 (%.3f)",
                    getattr(state, "champion_map50", 0.0),
                )
                champion_version = getattr(state, "champion_version", "")
                champion_map50 = getattr(state, "champion_map50", 0.0)

            wins = challenger_map50 > champion_map50
            promoted = False

            if wins and challenger_version:
                logger.info(
                    "Challenger %s (mAP=%.3f) beats champion %s (mAP=%.3f) — promoting",
                    challenger_version,
                    challenger_map50,
                    champion_version,
                    champion_map50,
                )
                try:
                    from harbor.ml.registry import ModelRegistry as _ModelReg

                    reg = _ModelReg()
                    await reg.alias("sdw-detector", challenger_version, "production")
                    promoted = True
                except Exception:
                    logger.warning("Failed to promote challenger via ModelRegistry.alias()")
            else:
                logger.info(
                    "Champion %s (mAP=%.3f) retains — challenger %s (mAP=%.3f) did not win",
                    champion_version,
                    champion_map50,
                    challenger_version,
                    challenger_map50,
                )

            return {
                "champion_version": champion_version,
                "champion_map50": champion_map50,
                "challenger_wins": wins,
                "promoted": promoted,
            }
        except Exception as exc:
            logger.exception("ChampionChallengerNode failed: %s", exc)
            return {"challenger_wins": False, "last_error": f"ChampionChallengerNode: {exc}"}


# ---------------------------------------------------------------------------
# Retrain Sub-Graph — Persist Model Metrics
# ---------------------------------------------------------------------------


class RetrainMetricsNode(NodeBase):
    """Write :class:`ModelMetrics` to the Postgres ``model_metrics`` table.

    Records version, mAP, precision, recall, training samples, and
    whether the model was promoted to production.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            from datetime import datetime

            from demos.sentinel_dark_watch.graph.state import ModelMetrics

            challenger_version: str = getattr(state, "challenger_version", "")
            challenger_map50: float = getattr(state, "challenger_map50", 0.0)
            merged_samples: int = getattr(state, "merged_training_samples", 0)
            promoted: bool = getattr(state, "promoted", False)

            metrics = ModelMetrics(
                version=challenger_version,
                map50=challenger_map50,
                training_samples=merged_samples,
                trained_at=datetime.now(UTC).isoformat(),
            )

            # Persist to Postgres
            try:
                import asyncpg

                conn = await asyncpg.connect(get_pg_dsn())
            except Exception:
                logger.warning("PostGIS unavailable — cannot persist model metrics")
                return {"retrain_metrics": metrics}

            try:
                await conn.execute(
                    "INSERT INTO model_metrics"
                    " (version, map50, precision_val, recall_val,"
                    "  training_samples, promoted)"
                    " VALUES ($1, $2, $3, $4, $5, $6)"
                    " ON CONFLICT (version) DO UPDATE SET"
                    "   map50 = EXCLUDED.map50,"
                    "   training_samples = EXCLUDED.training_samples,"
                    "   promoted = EXCLUDED.promoted",
                    metrics.version,
                    metrics.map50,
                    metrics.precision,
                    metrics.recall,
                    metrics.training_samples,
                    promoted,
                )
                logger.info("Persisted model metrics for version %s", metrics.version)
            except Exception:
                logger.warning("Failed to write model metrics to Postgres")
            finally:
                await conn.close()

            return {"retrain_metrics": metrics}
        except Exception as exc:
            logger.exception("RetrainMetricsNode failed: %s", exc)
            return {"last_error": f"RetrainMetricsNode: {exc}"}
