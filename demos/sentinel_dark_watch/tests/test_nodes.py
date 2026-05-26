"""Unit tests for Sentinel Dark Watch graph nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from demos.sentinel_dark_watch.graph.state import (
    Detection,
    RiskLevel,
    SdwState,
)
from demos.sentinel_dark_watch.graph.nodes import (
    AISCorrelationNode,
    NMSDeduplicationNode,
    RiskScoringNode,
)


@dataclass
class _MockCtx:
    """Minimal ExecutionContext stand-in."""
    run_id: str = "test-run"


# ---------------------------------------------------------------------------
# RiskScoringNode tests
# ---------------------------------------------------------------------------


async def test_risk_dark_vessel_sensitive_eez_critical(
    sample_detections: list[Detection],
) -> None:
    """Dark vessel in Iranian EEZ → score >= 80, level=Critical."""
    # Use only the first detection: dark, Iranian EEZ, far from port, large
    det = sample_detections[0].model_copy()
    state = SdwState(detections=[det])

    node = RiskScoringNode()
    result = await node.execute(state, _MockCtx())

    scored = result["detections"]
    assert len(scored) == 1
    assert scored[0].risk_score >= 80
    assert scored[0].risk_level == RiskLevel.CRITICAL


async def test_risk_ais_matched_near_port_low(
    sample_detections: list[Detection],
) -> None:
    """AIS-matched vessel near port → score < 40, level=Low."""
    # det-003: AIS-matched, near port, not dark, Omani EEZ
    det = sample_detections[2].model_copy()
    state = SdwState(detections=[det])

    node = RiskScoringNode()
    result = await node.execute(state, _MockCtx())

    scored = result["detections"]
    assert len(scored) == 1
    assert scored[0].risk_score < 40
    assert scored[0].risk_level == RiskLevel.LOW


async def test_risk_configurable_weights(
    sample_detections: list[Detection],
) -> None:
    """Custom weight values change the computed score."""
    det = sample_detections[0].model_copy()

    # All weights zeroed except confidence (max 20)
    state = SdwState(
        detections=[det],
        risk_weight_dark_vessel=0,
        risk_weight_sensitive_eez=0,
        risk_weight_far_from_port=0,
        risk_weight_large_vessel=0,
        risk_weight_confidence_max=20,
    )
    node = RiskScoringNode()
    result = await node.execute(state, _MockCtx())

    scored = result["detections"]
    assert len(scored) == 1
    # Only confidence contributes: int(0.92 * 20) = 18
    assert scored[0].risk_score == int(det.confidence * 20)
    assert scored[0].risk_level == RiskLevel.LOW


async def test_risk_empty_detections() -> None:
    """Empty detection list → pass-through, no error."""
    state = SdwState(detections=[])

    node = RiskScoringNode()
    result = await node.execute(state, _MockCtx())

    assert result["detections"] == []
    assert "last_error" not in result


async def test_risk_low_conf_flag(
    sample_detections: list[Detection],
) -> None:
    """Detection below low_conf_threshold → has_low_confidence_detections=True."""
    # det-005 has confidence 0.25, below default threshold 0.4
    det = sample_detections[4].model_copy()
    state = SdwState(detections=[det], low_conf_threshold=0.4)

    node = RiskScoringNode()
    result = await node.execute(state, _MockCtx())

    assert result["has_low_confidence_detections"] is True


# ---------------------------------------------------------------------------
# NMSDeduplicationNode tests
# ---------------------------------------------------------------------------


async def test_nms_overlapping_detections() -> None:
    """Two detections at same geo-coords → one remains after NMS."""
    # Same OBB corners = IoU 1.0 → suppressed
    corners = [[56.29, 26.54], [56.31, 26.54], [56.31, 26.56], [56.29, 26.56]]
    d1 = Detection(
        detection_id="dup-1",
        confidence=0.95,
        obb_corners=corners,
        geo_lat=26.55,
        geo_lon=56.30,
    )
    d2 = Detection(
        detection_id="dup-2",
        confidence=0.80,
        obb_corners=corners,
        geo_lat=26.55,
        geo_lon=56.30,
    )
    state = SdwState(raw_detections=[d1, d2])

    node = NMSDeduplicationNode()
    result = await node.execute(state, _MockCtx())

    assert result["detection_count"] == 1
    assert result["detections"][0].detection_id == "dup-1"  # higher confidence kept


async def test_nms_non_overlapping() -> None:
    """Two detections far apart → both preserved."""
    d1 = Detection(
        detection_id="far-1",
        confidence=0.90,
        obb_corners=[[56.0, 26.0], [56.02, 26.0], [56.02, 26.02], [56.0, 26.02]],
        geo_lat=26.01,
        geo_lon=56.01,
    )
    d2 = Detection(
        detection_id="far-2",
        confidence=0.85,
        obb_corners=[[57.0, 27.0], [57.02, 27.0], [57.02, 27.02], [57.0, 27.02]],
        geo_lat=27.01,
        geo_lon=57.01,
    )
    state = SdwState(raw_detections=[d1, d2])

    node = NMSDeduplicationNode()
    result = await node.execute(state, _MockCtx())

    assert result["detection_count"] == 2


async def test_nms_empty_list() -> None:
    """Empty detection list → empty result."""
    state = SdwState(raw_detections=[])

    node = NMSDeduplicationNode()
    result = await node.execute(state, _MockCtx())

    assert result["detections"] == []
    assert result["detection_count"] == 0


# ---------------------------------------------------------------------------
# AISCorrelationNode tests
# ---------------------------------------------------------------------------


def _make_ais_row(
    mmsi: str = "211000001",
    ship_name: str = "MV TEST",
    flag_state: str = "DE",
    vessel_type: str = "cargo",
    lat: float = 26.55,
    lon: float = 56.30,
    speed_kn: float = 0.0,
    heading: float = 0.0,
    timestamp: str = "2024-01-15T01:45:00Z",
) -> dict[str, Any]:
    """Build a dict that behaves like an asyncpg Record for AIS queries."""
    data = {
        "mmsi": mmsi,
        "ship_name": ship_name,
        "flag_state": flag_state,
        "vessel_type": vessel_type,
        "lat": lat,
        "lon": lon,
        "speed_kn": speed_kn,
        "heading": heading,
        "timestamp": timestamp,
    }
    return data


class _FakeRecord(dict):
    """Dict subclass supporting key-based access (like asyncpg Record)."""

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)


def _row_to_record(row: dict[str, Any]) -> _FakeRecord:
    return _FakeRecord(row)


async def test_ais_known_position_matched() -> None:
    """Detection near a known AIS position → dark_vessel=False, MMSI populated."""
    det = Detection(
        detection_id="ais-det-1",
        geo_lat=26.55,
        geo_lon=56.30,
        confidence=0.90,
        obb_corners=[[56.29, 26.54], [56.31, 26.54], [56.31, 26.56], [56.29, 26.56]],
    )
    from demos.sentinel_dark_watch.graph.state import TileMetadata

    state = SdwState(
        detections=[det],
        current_tile=TileMetadata(tile_id="tile-001", timestamp="2024-01-15T01:45:00Z"),
        ais_match_radius_m=5000,
    )

    # AIS position right at the detection location (speed 0 → predicted position = same)
    ais_rows = [_row_to_record(_make_ais_row(lat=26.55, lon=56.30, speed_kn=0.0))]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=ais_rows)

    with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
        node = AISCorrelationNode()
        result = await node.execute(state, _MockCtx())

    matched = result["detections"]
    assert len(matched) == 1
    assert matched[0].dark_vessel is False
    assert matched[0].ais_mmsi == "211000001"
    assert matched[0].ais_vessel_name == "MV TEST"


async def test_ais_no_match_dark_vessel() -> None:
    """Detection with no nearby AIS → dark_vessel=True."""
    det = Detection(
        detection_id="ais-det-2",
        geo_lat=26.55,
        geo_lon=56.30,
        confidence=0.90,
        obb_corners=[[56.29, 26.54], [56.31, 26.54], [56.31, 26.56], [56.29, 26.56]],
    )
    from demos.sentinel_dark_watch.graph.state import TileMetadata

    state = SdwState(
        detections=[det],
        current_tile=TileMetadata(tile_id="tile-001", timestamp="2024-01-15T01:45:00Z"),
        ais_match_radius_m=500,
    )

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
        node = AISCorrelationNode()
        result = await node.execute(state, _MockCtx())

    matched = result["detections"]
    assert len(matched) == 1
    assert matched[0].dark_vessel is True


async def test_ais_broker_failure_conservative() -> None:
    """Mock DB failure → all detections marked dark_vessel=True."""
    det = Detection(
        detection_id="ais-det-3",
        geo_lat=26.55,
        geo_lon=56.30,
        confidence=0.90,
        obb_corners=[[56.29, 26.54], [56.31, 26.54], [56.31, 26.56], [56.29, 26.56]],
    )
    from demos.sentinel_dark_watch.graph.state import TileMetadata

    state = SdwState(
        detections=[det],
        current_tile=TileMetadata(tile_id="tile-001", timestamp="2024-01-15T01:45:00Z"),
    )

    with patch("asyncpg.connect", AsyncMock(side_effect=ConnectionError("DB down"))):
        node = AISCorrelationNode()
        result = await node.execute(state, _MockCtx())

    matched = result["detections"]
    assert len(matched) == 1
    assert matched[0].dark_vessel is True
    assert "last_error" in result
