"""Shared pytest fixtures for Sentinel Dark Watch tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from demos.sentinel_dark_watch.graph.state import (
    Detection,
    RiskLevel,
    SdwState,
    TileMetadata,
)


@pytest.fixture()
def sample_state() -> SdwState:
    """SdwState with realistic defaults for testing."""
    return SdwState(
        run_id="test-run-001",
        tile_queue=["tile_hormuz_001", "tile_hormuz_002"],
        model_version="v1.0",
    )


@pytest.fixture()
def sample_detections() -> list[Detection]:
    """Five detections with varied attributes for scoring/filter tests."""
    return [
        # Dark vessel, high confidence, in sensitive EEZ, far from port
        Detection(
            detection_id="det-001",
            tile_id="tile_hormuz_001",
            geo_lat=26.55,
            geo_lon=56.30,
            confidence=0.92,
            obb_corners=[[56.29, 26.54], [56.31, 26.54], [56.31, 26.56], [56.29, 26.56]],
            vessel_length_m=150.0,
            dark_vessel=True,
            eez_name="Iranian",
            distance_to_port_nm=85.0,
            distance_to_coast_nm=40.0,
        ),
        # Dark vessel, high confidence, different location
        Detection(
            detection_id="det-002",
            tile_id="tile_hormuz_001",
            geo_lat=26.70,
            geo_lon=56.50,
            confidence=0.88,
            obb_corners=[[56.49, 26.69], [56.51, 26.69], [56.51, 26.71], [56.49, 26.71]],
            vessel_length_m=120.0,
            dark_vessel=True,
            eez_name="Iranian",
            distance_to_port_nm=70.0,
            distance_to_coast_nm=35.0,
        ),
        # AIS-matched vessel near port
        Detection(
            detection_id="det-003",
            tile_id="tile_hormuz_002",
            geo_lat=25.40,
            geo_lon=55.30,
            confidence=0.95,
            obb_corners=[[55.29, 25.39], [55.31, 25.39], [55.31, 25.41], [55.29, 25.41]],
            vessel_length_m=80.0,
            dark_vessel=False,
            ais_mmsi="211234567",
            ais_vessel_name="MV TRADE WIND",
            ais_flag_state="DE",
            ais_vessel_type="cargo",
            eez_name="Omani",
            distance_to_port_nm=5.0,
            distance_to_coast_nm=2.0,
        ),
        # On land — would be filtered by LandMaskFilterNode
        Detection(
            detection_id="det-004",
            tile_id="tile_hormuz_002",
            geo_lat=26.10,
            geo_lon=56.00,
            confidence=0.60,
            obb_corners=[[55.99, 26.09], [56.01, 26.09], [56.01, 26.11], [55.99, 26.11]],
            vessel_length_m=30.0,
            dark_vessel=False,
            eez_name="",
            distance_to_port_nm=10.0,
            distance_to_coast_nm=0.0,
        ),
        # Low confidence detection
        Detection(
            detection_id="det-005",
            tile_id="tile_hormuz_001",
            geo_lat=26.80,
            geo_lon=56.70,
            confidence=0.25,
            obb_corners=[[56.69, 26.79], [56.71, 26.79], [56.71, 26.81], [56.69, 26.81]],
            vessel_length_m=45.0,
            dark_vessel=True,
            eez_name="Iranian",
            distance_to_port_nm=60.0,
            distance_to_coast_nm=25.0,
        ),
    ]


@pytest.fixture()
def sample_tile_metadata() -> TileMetadata:
    """TileMetadata for a Strait of Hormuz scene."""
    return TileMetadata(
        tile_id="tile_hormuz_001",
        scene_id="S1A_IW_GRDH_20240115T014500",
        file_path="/data/tiles/tile_hormuz_001.tif",
        timestamp="2024-01-15T01:45:00Z",
        bounds_wkt="POLYGON((56.0 26.0, 57.0 26.0, 57.0 27.0, 56.0 27.0, 56.0 26.0))",
        patch_count=42,
    )


@pytest.fixture()
def mock_pg_pool() -> AsyncMock:
    """AsyncMock for an asyncpg connection pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool
