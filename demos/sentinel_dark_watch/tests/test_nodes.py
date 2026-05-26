"""Unit tests for Sentinel Dark Watch graph nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from demos.sentinel_dark_watch.graph.state import (
    Detection,
    RiskLevel,
    SdwState,
)
from demos.sentinel_dark_watch.graph.nodes import (
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
