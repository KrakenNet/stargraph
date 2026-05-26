"""Integration test: full pipeline mock run + bootstrap idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from demos.sentinel_dark_watch.graph.state import (
    Detection,
    RiskLevel,
    SdwState,
    TileMetadata,
)


@dataclass
class _MockCtx:
    """Minimal ExecutionContext stand-in."""

    run_id: str = "integration-test-run"


# ---------------------------------------------------------------------------
# 3.9 — Integration test for full pipeline (mock mode)
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline_state() -> SdwState:
    """SdwState pre-populated with raw detections (skipping YOLO inference).

    Simulates the output of SARIngestNode + YOLOInferenceNode so the
    enrichment → scoring → reporting → metrics flow can be tested
    end-to-end.
    """
    return SdwState(
        run_id="integ-run-001",
        run_started_at="2024-01-15T01:00:00Z",
        tile_queue=["tile_hormuz_001"],
        model_version="v1.0",
    )


@pytest.fixture()
def raw_detections() -> list[Detection]:
    """Five raw detections as if produced by YOLOInferenceNode."""
    return [
        Detection(
            detection_id="integ-det-001",
            tile_id="tile_hormuz_001",
            geo_lat=26.55,
            geo_lon=56.30,
            confidence=0.92,
            obb_corners=[[56.29, 26.54], [56.31, 26.54], [56.31, 26.56], [56.29, 26.56]],
            vessel_length_m=150.0,
        ),
        Detection(
            detection_id="integ-det-002",
            tile_id="tile_hormuz_001",
            geo_lat=26.70,
            geo_lon=56.50,
            confidence=0.88,
            obb_corners=[[56.49, 26.69], [56.51, 26.69], [56.51, 26.71], [56.49, 26.71]],
            vessel_length_m=120.0,
        ),
        Detection(
            detection_id="integ-det-003",
            tile_id="tile_hormuz_001",
            geo_lat=25.40,
            geo_lon=55.30,
            confidence=0.95,
            obb_corners=[[55.29, 25.39], [55.31, 25.39], [55.31, 25.41], [55.29, 25.41]],
            vessel_length_m=80.0,
        ),
    ]


def _apply_patch(state: SdwState, patch: dict[str, Any]) -> SdwState:
    """Merge a node result dict into state (mimics execution loop)."""
    data = state.model_dump()
    data.update(patch)
    return SdwState(**data)


async def test_pipeline_mock_run(
    pipeline_state: SdwState,
    raw_detections: list[Detection],
) -> None:
    """Run each enrichment pipeline node in sequence through mock state.

    Skips YOLOInferenceNode (needs real ONNX model). Starts from
    pre-populated detections and verifies the data flow:
    SARIngestNode → NMSDeduplicationNode → LandMaskFilterNode →
    AISCorrelationNode → GeoContextNode → RiskScoringNode →
    ReportingNode → MetricsCollectorNode.
    """
    from demos.sentinel_dark_watch.graph.nodes import (
        AISCorrelationNode,
        GeoContextNode,
        LandMaskFilterNode,
        MetricsCollectorNode,
        NMSDeduplicationNode,
        ReportingNode,
        RiskScoringNode,
        SARIngestNode,
    )

    ctx = _MockCtx()
    state = pipeline_state

    # --- 1. SARIngestNode ---------------------------------------------------
    # Mock DB returning tile metadata; use __file__ as existing file_path
    tile_row = {"scene_id": "S1A_TEST", "file_path": __file__,
                "acquired_at": "2024-01-15T01:45:00Z", "bounds_wkt": None}

    class _FakeRecord(dict):
        def __getitem__(self, key: str) -> Any:
            return super().__getitem__(key)

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=_FakeRecord(tile_row))

    with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
        result = await SARIngestNode().execute(state, ctx)

    state = _apply_patch(state, result)
    assert state.current_tile_id == "tile_hormuz_001"
    assert state.current_tile.scene_id == "S1A_TEST"
    assert state.pipeline_phase == "ingest"

    # Inject raw_detections (simulating YOLOInferenceNode output)
    state = _apply_patch(state, {"raw_detections": raw_detections})

    # --- 2. NMSDeduplicationNode -------------------------------------------
    result = await NMSDeduplicationNode().execute(state, ctx)
    state = _apply_patch(state, result)
    assert state.detection_count == 3  # no overlaps in fixture
    assert len(state.detections) == 3
    assert state.pipeline_phase == "nms"

    # --- 3. LandMaskFilterNode ---------------------------------------------
    # Mock: no detections on land
    with (
        patch("asyncpg.connect", AsyncMock(return_value=AsyncMock())),
        patch(
            "demos.sentinel_dark_watch.geo.point_on_land",
            AsyncMock(return_value=False),
        ),
    ):
        result = await LandMaskFilterNode().execute(state, ctx)

    state = _apply_patch(state, result)
    assert len(state.detections) == 3  # all at sea
    assert state.pipeline_phase == "land_filter"

    # --- 4. AISCorrelationNode ---------------------------------------------
    # Mock: one AIS match near det-003, rest are dark vessels
    ais_row = _FakeRecord({
        "mmsi": "211999888",
        "ship_name": "MV INTEG TEST",
        "flag_state": "DE",
        "vessel_type": "cargo",
        "lat": 25.40,
        "lon": 55.30,
        "speed_kn": 0.0,
        "heading": 0.0,
        "timestamp": "2024-01-15T01:45:00Z",
    })

    mock_ais_conn = AsyncMock()
    mock_ais_conn.fetch = AsyncMock(return_value=[ais_row])
    mock_ais_conn.close = AsyncMock()

    with patch("asyncpg.connect", AsyncMock(return_value=mock_ais_conn)):
        result = await AISCorrelationNode().execute(state, ctx)

    state = _apply_patch(state, result)
    assert state.pipeline_phase == "ais_correlation"

    # det-003 should be AIS-matched (same coords as AIS row)
    matched = [d for d in state.detections if not d.dark_vessel]
    dark = [d for d in state.detections if d.dark_vessel]
    assert len(matched) >= 1
    assert any(d.ais_mmsi == "211999888" for d in matched)
    assert len(dark) >= 1  # at least det-001 or det-002 is dark

    # --- 5. GeoContextNode -------------------------------------------------
    mock_geo_conn = AsyncMock()

    with (
        patch("asyncpg.connect", AsyncMock(return_value=mock_geo_conn)),
        patch(
            "demos.sentinel_dark_watch.geo.point_in_eez",
            AsyncMock(return_value="Iranian"),
        ),
        patch(
            "demos.sentinel_dark_watch.geo.nearest_port",
            AsyncMock(return_value=("Bandar Abbas", 80_000.0)),
        ),
        patch(
            "demos.sentinel_dark_watch.geo.nearest_coast_distance_m",
            AsyncMock(return_value=30_000.0),
        ),
        patch("demos.sentinel_dark_watch.graph.signatures.DSPY_AVAILABLE", False),
    ):
        result = await GeoContextNode().execute(state, ctx)

    state = _apply_patch(state, result)
    assert state.pipeline_phase == "geo_context"
    for det in state.detections:
        assert det.eez_name == "Iranian"
        assert det.distance_to_port_nm > 0
        assert det.distance_to_coast_nm > 0
        assert det.geo_summary  # non-empty templated fallback

    # --- 6. RiskScoringNode ------------------------------------------------
    result = await RiskScoringNode().execute(state, ctx)
    state = _apply_patch(state, result)
    assert state.pipeline_phase == "risk_scoring"
    for det in state.detections:
        assert det.risk_score > 0
        assert det.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW)

    # Dark vessels in Iranian EEZ with high confidence → Critical or High
    dark_dets = [d for d in state.detections if d.dark_vessel]
    for d in dark_dets:
        assert d.risk_score >= 60, f"Dark vessel {d.detection_id} should be High+ risk"

    # --- 7. ReportingNode --------------------------------------------------
    with patch("demos.sentinel_dark_watch.graph.signatures.DSPY_AVAILABLE", False):
        result = await ReportingNode().execute(state, ctx)

    state = _apply_patch(state, result)
    assert state.pipeline_phase == "reporting"
    for det in state.detections:
        assert det.report_text
        assert "Detection Summary" in det.report_text
        assert "Risk Assessment" in det.report_text

    # --- 8. MetricsCollectorNode -------------------------------------------
    mock_metrics_conn = AsyncMock()
    mock_metrics_conn.execute = AsyncMock(return_value="INSERT 0 1")
    mock_metrics_conn.close = AsyncMock()

    with patch("asyncpg.connect", AsyncMock(return_value=mock_metrics_conn)):
        result = await MetricsCollectorNode().execute(state, ctx)

    state = _apply_patch(state, result)
    assert state.pipeline_phase == "metrics"
    assert state.run_metrics is not None

    # --- Final assertions: full data flow verified -------------------------
    assert len(state.detections) == 3
    assert all(d.risk_score > 0 for d in state.detections)
    assert all(d.report_text for d in state.detections)
    assert all(d.eez_name for d in state.detections)
    assert all(d.geo_summary for d in state.detections)


# ---------------------------------------------------------------------------
# 3.10 — Bootstrap idempotency test
# ---------------------------------------------------------------------------


def test_bootstrap_idempotent() -> None:
    """Verify schema.sql uses CREATE TABLE IF NOT EXISTS and seed uses ON CONFLICT DO NOTHING."""
    from pathlib import Path

    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    bootstrap_path = Path(__file__).resolve().parent.parent / "bootstrap.py"

    # --- schema.sql: all CREATE statements must be IF NOT EXISTS ---
    schema_sql = schema_path.read_text(encoding="utf-8")

    # Find all CREATE TABLE / CREATE INDEX / CREATE EXTENSION statements
    create_lines = [
        line.strip()
        for line in schema_sql.splitlines()
        if line.strip().upper().startswith("CREATE ")
    ]
    assert len(create_lines) > 0, "schema.sql should have CREATE statements"

    for line in create_lines:
        assert "IF NOT EXISTS" in line.upper(), (
            f"Missing IF NOT EXISTS in: {line[:80]}"
        )

    # --- bootstrap.py: all INSERT seed data must use ON CONFLICT DO NOTHING ---
    bootstrap_src = bootstrap_path.read_text(encoding="utf-8")

    # Find all INSERT statements in bootstrap.py
    insert_lines = [
        line.strip()
        for line in bootstrap_src.splitlines()
        if "INSERT INTO" in line.upper()
    ]
    # Each INSERT block should have a corresponding ON CONFLICT DO NOTHING
    # Check the full source — every INSERT INTO block must end with ON CONFLICT
    import re

    insert_blocks = re.findall(
        r"INSERT\s+INTO\s+\S+.*?(?=INSERT\s+INTO|\Z)",
        bootstrap_src,
        re.DOTALL | re.IGNORECASE,
    )
    assert len(insert_blocks) > 0, "bootstrap.py should have INSERT statements"

    for block in insert_blocks:
        assert "ON CONFLICT" in block.upper(), (
            f"Missing ON CONFLICT in INSERT block: {block[:100]}..."
        )
