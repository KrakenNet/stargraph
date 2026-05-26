# SPDX-License-Identifier: Apache-2.0
"""DSPy signatures and LLM fallback templates for SDW nodes."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback templates (used when DSPy / LLM is unavailable)
# ---------------------------------------------------------------------------

FALLBACK_TEMPLATES: dict[str, str] = {
    "geo_context": (
        "Vessel detected at ({lat}N, {lon}E) in {eez_name}, "
        "{distance_to_port_nm:.0f}nm from nearest port. "
        "AIS status: {ais_status}."
    ),
    "reporting": (
        "## Detection Summary\n"
        "{detection_count} vessel detection(s) from tile {tile_id}. "
        "{dark_count} dark vessel(s) identified.\n\n"
        "## Imagery Reference\n"
        "Source tile: {tile_id} | Scene: {scene_id}\n\n"
        "## AIS Correlation\n"
        "{ais_matched} detection(s) matched to AIS transponders. "
        "{dark_count} unmatched (dark).\n\n"
        "## Geo-Context\n"
        "{geo_summary}\n\n"
        "## Risk Assessment\n"
        "Overall risk: {risk_level} (score {risk_score}/100).\n\n"
        "## Recommended Actions\n"
        "{actions}"
    ),
}

# ---------------------------------------------------------------------------
# DSPy Signature classes — imported lazily by nodes
# ---------------------------------------------------------------------------

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

    DSPY_AVAILABLE = True

except ImportError:
    logger.info("DSPy not installed — signatures unavailable, nodes will use fallback templates")
    DSPY_AVAILABLE = False
