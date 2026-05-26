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


# ---------------------------------------------------------------------------
# Land-Mask Filter
# ---------------------------------------------------------------------------


class LandMaskFilterNode(NodeBase):
    """Filter out detections whose centroid falls on land.

    Queries PostGIS ``coastlines`` table with ``ST_Contains``.  If PostGIS
    is unreachable the filter is skipped (all detections kept) with a
    warning — we never discard valid detections due to infra failure.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
        if not detections:
            return {"detections": [], "detection_count": 0, "pipeline_phase": "land_filter"}

        try:
            import asyncpg

            dsn = os.environ.get(
                "POSTGRES_DSN",
                "postgresql://harbor:harbor@localhost:5441/sdw",
            )
            conn = await asyncpg.connect(dsn)
        except Exception:
            log.warning("PostGIS unavailable — skipping land-mask filter")
            return {"pipeline_phase": "land_filter"}

        try:
            water_detections: list[Any] = []
            for det in detections:
                try:
                    on_land = await conn.fetchval(
                        "SELECT EXISTS("
                        "  SELECT 1 FROM coastlines"
                        "  WHERE ST_Contains("
                        "    geom,"
                        "    ST_SetSRID(ST_MakePoint($1, $2), 4326)"
                        "  )"
                        ")",
                        det.geo_lon,
                        det.geo_lat,
                    )
                    if not on_land:
                        water_detections.append(det)
                except Exception:
                    # On per-detection query failure, keep the detection
                    log.warning(
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


# ---------------------------------------------------------------------------
# AIS Correlation — predicted-position matching
# ---------------------------------------------------------------------------


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    R = 6_371_000.0  # Earth radius in metres
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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

        try:
            import asyncpg

            dsn = os.environ.get(
                "POSTGRES_DSN",
                "postgresql://harbor:harbor@localhost:5441/sdw",
            )
            conn = await asyncpg.connect(dsn)
        except Exception:
            log.warning("PostGIS unavailable — marking all detections as dark vessels")
            for det in detections:
                det.dark_vessel = True
            return {"detections": detections, "pipeline_phase": "ais_correlation"}

        try:
            # Query AIS positions in bounding box + time window
            if acq_ts:
                rows = await conn.fetch(
                    "SELECT mmsi, ship_name, flag_state, vessel_type,"
                    "       lat, lon, speed_kn, heading, timestamp"
                    "  FROM ais_positions"
                    " WHERE lat BETWEEN $1 AND $2"
                    "   AND lon BETWEEN $3 AND $4"
                    "   AND timestamp BETWEEN $5::timestamptz - ($6 || ' minutes')::interval"
                    "                      AND $5::timestamptz + ($6 || ' minutes')::interval",
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                    acq_ts,
                    str(time_window_min),
                )
            else:
                # No acquisition time — get all AIS in bounding box
                rows = await conn.fetch(
                    "SELECT mmsi, ship_name, flag_state, vessel_type,"
                    "       lat, lon, speed_kn, heading, timestamp"
                    "  FROM ais_positions"
                    " WHERE lat BETWEEN $1 AND $2"
                    "   AND lon BETWEEN $3 AND $4",
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                )
        except Exception:
            log.warning("AIS query failed — marking all detections as dark vessels")
            for det in detections:
                det.dark_vessel = True
            return {"detections": detections, "pipeline_phase": "ais_correlation"}
        finally:
            await conn.close()

        # Compute predicted positions for each AIS report
        ais_predicted: list[dict[str, Any]] = []
        for row in rows:
            speed_kn = float(row["speed_kn"]) if row["speed_kn"] else 0.0
            heading_deg = float(row["heading"]) if row["heading"] else 0.0
            heading_rad = math.radians(heading_deg)

            # Time delta in hours
            dt_hours = 0.0
            if acq_ts and row["timestamp"]:
                try:
                    from datetime import datetime, timezone

                    ais_time = row["timestamp"]
                    if isinstance(ais_time, str):
                        ais_time = datetime.fromisoformat(ais_time)
                    if isinstance(acq_ts, str):
                        acq_dt = datetime.fromisoformat(acq_ts)
                    else:
                        acq_dt = acq_ts
                    # Ensure timezone-aware
                    if ais_time.tzinfo is None:
                        ais_time = ais_time.replace(tzinfo=timezone.utc)
                    if acq_dt.tzinfo is None:
                        acq_dt = acq_dt.replace(tzinfo=timezone.utc)
                    dt_hours = (acq_dt - ais_time).total_seconds() / 3600.0
                except Exception:
                    dt_hours = 0.0

            lat = float(row["lat"])
            lon = float(row["lon"])
            predicted_lat = lat + speed_kn * math.cos(heading_rad) * dt_hours / 60.0
            predicted_lon = lon + speed_kn * math.sin(heading_rad) * dt_hours / 60.0

            ais_predicted.append({
                "mmsi": str(row["mmsi"]),
                "ship_name": row["ship_name"] or "",
                "flag_state": row["flag_state"] or "",
                "vessel_type": row["vessel_type"] or "",
                "predicted_lat": predicted_lat,
                "predicted_lon": predicted_lon,
            })

        # Match detections to AIS by minimum distance within radius
        matched_det_indices: set[int] = set()
        matched_ais_indices: set[int] = set()

        # Build distance matrix and greedily match closest pairs
        pairs: list[tuple[float, int, int]] = []
        for di, det in enumerate(detections):
            for ai, ais in enumerate(ais_predicted):
                dist = _haversine_m(
                    det.geo_lat, det.geo_lon,
                    ais["predicted_lat"], ais["predicted_lon"],
                )
                if dist <= match_radius_m:
                    pairs.append((dist, di, ai))

        pairs.sort(key=lambda x: x[0])
        for dist, di, ai in pairs:
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


# ---------------------------------------------------------------------------
# Geo-Context Enrichment — PostGIS + DSPy synthesis
# ---------------------------------------------------------------------------

_GEO_FALLBACK_TEMPLATE = (
    "Vessel detected at ({lat}N, {lon}E) in {eez_name}, "
    "{distance_to_port_nm:.0f}nm from nearest port. "
    "AIS status: {ais_status}."
)


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
        detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
        if not detections:
            return {"detections": [], "pipeline_phase": "geo_context"}

        # Step 1 — PostGIS enrichment
        conn = None
        try:
            import asyncpg

            dsn = os.environ.get(
                "POSTGRES_DSN",
                "postgresql://harbor:harbor@localhost:5441/sdw",
            )
            conn = await asyncpg.connect(dsn)
        except Exception:
            log.warning("PostGIS unavailable — skipping geo-context enrichment")

        for det in detections:
            if conn is not None:
                try:
                    # EEZ lookup via ST_Contains
                    eez_row = await conn.fetchrow(
                        "SELECT name FROM eez_boundaries"
                        " WHERE ST_Contains("
                        "   geom, ST_SetSRID(ST_MakePoint($1, $2), 4326)"
                        " ) LIMIT 1",
                        det.geo_lon,
                        det.geo_lat,
                    )
                    if eez_row:
                        det.eez_name = eez_row["name"]

                    # Nearest port distance (metres → nautical miles)
                    port_row = await conn.fetchrow(
                        "SELECT name,"
                        "  ST_Distance("
                        "    geom::geography,"
                        "    ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography"
                        "  ) AS dist_m"
                        " FROM ports ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)"
                        " LIMIT 1",
                        det.geo_lon,
                        det.geo_lat,
                    )
                    if port_row:
                        det.distance_to_port_nm = float(port_row["dist_m"]) / 1852.0

                    # Nearest coastline distance (metres → nautical miles)
                    coast_row = await conn.fetchval(
                        "SELECT ST_Distance("
                        "  geom::geography,"
                        "  ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography"
                        ") FROM coastlines"
                        " ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)"
                        " LIMIT 1",
                        det.geo_lon,
                        det.geo_lat,
                    )
                    if coast_row is not None:
                        det.distance_to_coast_nm = float(coast_row) / 1852.0

                except Exception:
                    log.warning(
                        "Geo query failed for detection %s — using defaults",
                        det.detection_id,
                    )

        if conn is not None:
            await conn.close()

        # Step 2 — DSPy ChainOfThought synthesis for geo_summary
        dspy_available = False
        try:
            import dspy

            class GeoContextSignature(dspy.Signature):
                """Synthesize a concise geographic context summary for a maritime detection."""

                detection_lat: float = dspy.InputField(desc="Detection latitude")
                detection_lon: float = dspy.InputField(desc="Detection longitude")
                dark_vessel: bool = dspy.InputField(desc="Whether vessel is dark (no AIS)")
                eez_name: str = dspy.InputField(desc="Exclusive Economic Zone name")
                distance_to_port_nm: float = dspy.InputField(desc="Distance to nearest port in NM")
                nearest_port_name: str = dspy.InputField(desc="Name of nearest port")
                distance_to_coast_nm: float = dspy.InputField(desc="Distance to coast in NM")
                ais_status: str = dspy.InputField(desc="AIS correlation status")
                geo_summary: str = dspy.OutputField(desc="Human-readable geographic context summary")

            cot = dspy.ChainOfThought(GeoContextSignature)
            dspy_available = True
        except ImportError:
            log.info("DSPy not available — using templated fallback for geo_summary")

        for det in detections:
            ais_status = "dark (no AIS)" if det.dark_vessel else f"AIS matched ({det.ais_mmsi or 'unknown'})"

            if dspy_available:
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
                    log.warning("DSPy geo-context call failed — using fallback")

            # Templated fallback
            det.geo_summary = _GEO_FALLBACK_TEMPLATE.format(
                lat=det.geo_lat,
                lon=det.geo_lon,
                eez_name=det.eez_name or "Unknown EEZ",
                distance_to_port_nm=det.distance_to_port_nm,
                ais_status=ais_status,
            )

        return {"detections": detections, "pipeline_phase": "geo_context"}


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


class RiskScoringNode(NodeBase):
    """Apply configurable risk scoring formula to each detection.

    Scoring weights come from state fields (``risk_weight_*``),
    allowing per-run or env-var overrides (AC-6.4).

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
        from demos.sentinel_dark_watch.graph.state import RiskLevel

        detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
        if not detections:
            return {"detections": [], "pipeline_phase": "risk_scoring"}

        sensitive_eezs = _load_sensitive_eezs()

        w_dark = state.risk_weight_dark_vessel  # type: ignore[attr-defined]
        w_eez = state.risk_weight_sensitive_eez  # type: ignore[attr-defined]
        w_port = state.risk_weight_far_from_port  # type: ignore[attr-defined]
        w_vessel = state.risk_weight_large_vessel  # type: ignore[attr-defined]
        w_conf_max = state.risk_weight_confidence_max  # type: ignore[attr-defined]
        low_conf_threshold: float = state.low_conf_threshold  # type: ignore[attr-defined]

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


# ---------------------------------------------------------------------------
# Reporting — DSPy narrative synthesis with templated fallback
# ---------------------------------------------------------------------------

_REPORT_FALLBACK_TEMPLATE = """## Detection Summary
{detection_count} vessel detection(s) from tile {tile_id}. {dark_count} dark vessel(s) identified.

## Imagery Reference
Source tile: {tile_id} | Scene: {scene_id}

## AIS Correlation
{ais_matched} detection(s) matched to AIS transponders. {dark_count} unmatched (dark).

## Geo-Context
{geo_summary}

## Risk Assessment
Overall risk: {risk_level} (score {risk_score}/100).

## Recommended Actions
{actions}"""


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
            actions_list.append("- IMMEDIATE: Alert maritime authorities and initiate tracking.")
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
        dspy_ok = False
        try:
            import dspy

            class ReportingSignature(dspy.Signature):
                """Synthesize a concise maritime intelligence report from detection data."""

                detection_count: int = dspy.InputField(desc="Number of detections")
                dark_vessel_count: int = dspy.InputField(desc="Number of dark vessels")
                ais_matched_count: int = dspy.InputField(desc="Number of AIS-matched detections")
                overall_risk_level: str = dspy.InputField(desc="Highest risk level")
                max_risk_score: int = dspy.InputField(desc="Highest risk score (0-100)")
                geo_summary: str = dspy.InputField(desc="Combined geo-context summaries")
                tile_id: str = dspy.InputField(desc="Source tile identifier")
                recommended_actions: str = dspy.InputField(desc="Recommended actions list")
                report: str = dspy.OutputField(desc="Full structured maritime intelligence report")

            cot = dspy.ChainOfThought(ReportingSignature)
            dspy_ok = True
        except ImportError:
            log.info("DSPy not available — using templated fallback for report")

        if dspy_ok:
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
                log.warning("DSPy reporting call failed — using fallback")
                dspy_ok = False

        if not dspy_ok:
            report_text = _REPORT_FALLBACK_TEMPLATE.format(
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
        detections: list[Any] = list(state.detections)  # type: ignore[attr-defined]
        if not detections:
            return {"detections": [], "pipeline_phase": "emit_chips"}

        tile = state.current_tile  # type: ignore[attr-defined]
        file_path = tile.file_path if tile else ""

        # Guard: rasterio + Pillow may not be installed
        try:
            import numpy as np
            import rasterio  # noqa: F811
        except ImportError:
            log.warning("rasterio/numpy not installed — skipping SAR chip extraction")
            return {"detections": detections, "pipeline_phase": "emit_chips"}

        try:
            from PIL import Image  # noqa: F811
        except ImportError:
            log.warning("Pillow not installed — skipping SAR chip extraction")
            return {"detections": detections, "pipeline_phase": "emit_chips"}

        if not file_path or not Path(file_path).exists():
            log.warning("Tile file not found: %s — skipping chip extraction", file_path)
            return {"detections": detections, "pipeline_phase": "emit_chips"}

        # Open source GeoTIFF once, crop per-detection
        try:
            src = rasterio.open(file_path)
        except Exception:
            log.warning("Failed to open GeoTIFF %s — skipping chips", file_path)
            return {"detections": detections, "pipeline_phase": "emit_chips"}

        try:
            img = src.read()  # (C, H, W)
            transform = src.transform
            _, h, w = img.shape

            # Ensure (H, W, C) for Pillow
            if img.ndim == 3:
                img_hwc = img.transpose(1, 2, 0)
            else:
                img_hwc = img

            half = self._CHIP_SIZE // 2
            chip_dir = Path(file_path).parent / "chips"
            chip_dir.mkdir(parents=True, exist_ok=True)

            for det in detections:
                try:
                    # Geo-coords → pixel coords via inverse affine
                    inv = ~transform
                    px_x, px_y = inv * (det.geo_lon, det.geo_lat)
                    px_x, px_y = int(round(px_x)), int(round(px_y))

                    # Crop bounds (clamped to image)
                    r0 = max(0, px_y - half)
                    r1 = min(h, px_y + half)
                    c0 = max(0, px_x - half)
                    c1 = min(w, px_x + half)

                    if r1 - r0 < 2 or c1 - c0 < 2:
                        log.warning("Chip too small for detection %s — skipping", det.detection_id)
                        continue

                    chip_arr = img_hwc[r0:r1, c0:c1]

                    # Normalize to uint8 for PNG
                    if chip_arr.dtype != np.uint8:
                        cmin, cmax = chip_arr.min(), chip_arr.max()
                        if cmax > cmin:
                            chip_arr = ((chip_arr - cmin) / (cmax - cmin) * 255).astype(np.uint8)
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
                    log.warning(
                        "Failed to extract chip for detection %s — continuing",
                        det.detection_id,
                    )
                    continue
        finally:
            src.close()

        return {"detections": detections, "pipeline_phase": "emit_chips"}
