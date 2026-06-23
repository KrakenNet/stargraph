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

    # ----- Evolution signatures -----

    class WeaknessAnalysisSignature(dspy.Signature):
        """Identify specific weaknesses and improvement opportunities for a maritime SAR detection pipeline."""

        recent_run_count: int = dspy.InputField(desc="Number of recent pipeline runs analyzed")
        avg_detections: float = dspy.InputField(desc="Average detections per run")
        false_positive_rate: float = dspy.InputField(desc="False positive rate (0-1)")
        processing_seconds: float = dspy.InputField(desc="Average processing time in seconds")
        model_map50: float = dspy.InputField(desc="Current model mAP@50 score")
        dark_vessel_rate: float = dspy.InputField(desc="Rate of dark (unmatched) vessels")
        weaknesses: str = dspy.OutputField(
            desc="Comma-separated list of specific pipeline weaknesses"
        )
        opportunities: str = dspy.OutputField(
            desc="Comma-separated list of actionable improvement opportunities"
        )

    class ResearchAlternativesSignature(dspy.Signature):
        """Propose specific, implementable improvements for a maritime SAR vessel detection system."""

        weaknesses: str = dspy.InputField(desc="Identified pipeline weaknesses")
        opportunities: str = dspy.InputField(desc="Improvement opportunities identified")
        current_approach: str = dspy.InputField(desc="Current detection approach summary")
        proposals: str = dspy.OutputField(
            desc="3-5 specific proposals, each as: TITLE | CATEGORY (threshold/hyperparameter/"
            "preprocessing/model_architecture) | RISK (low/medium/high) | EXPECTED_IMPROVEMENT"
        )

    class ProposalEvaluationSignature(dspy.Signature):
        """Evaluate whether a pipeline improvement proposal is worth experimenting with."""

        title: str = dspy.InputField(desc="Proposal title")
        category: str = dspy.InputField(desc="Proposal category")
        risk: str = dspy.InputField(desc="Risk level")
        description: str = dspy.InputField(desc="Proposal description")
        current_metrics: str = dspy.InputField(desc="Current pipeline metrics summary")
        proceed: str = dspy.OutputField(desc="YES or NO")
        reasoning: str = dspy.OutputField(desc="Brief reasoning for the decision")

    DSPY_AVAILABLE = True

    # ----- ReAct tools for evolution agents -----

    def query_pipeline_metrics(query: str) -> str:
        """Query recent pipeline run metrics from the database.
        Useful for understanding detection performance, processing times,
        false positive rates, and trends across runs.

        Args:
            query: What metrics to look up, e.g. "last 5 runs detection counts",
                   "average processing time", "false positive trend"
        """
        import psycopg

        from demos.sentinel_dark_watch.db import get_pg_dsn

        try:
            dsn = get_pg_dsn()
            conn = psycopg.connect(dsn)
            cur = conn.cursor()
            cur.execute(
                "SELECT total_detections, dark_vessels, ais_matched,"
                " false_positives, processing_secs, created_at"
                " FROM run_metrics ORDER BY created_at DESC LIMIT 20"
            )
            rows = cur.fetchall()
            if not rows:
                conn.close()
                return "No pipeline runs recorded yet."
            cols = [d[0] for d in cur.description]
            lines = [f"Last {len(rows)} pipeline runs:"]
            for r in rows:
                d = dict(zip(cols, r))
                lines.append(
                    f"  {d['created_at']}: "
                    f"detections={d['total_detections']}, "
                    f"dark={d['dark_vessels']}, "
                    f"FP={d['false_positives']}, "
                    f"time={d['processing_secs']:.0f}s"
                )
            cur.execute(
                "SELECT title, decision, delta_pct, risk, created_at"
                " FROM evolution_log ORDER BY created_at DESC LIMIT 5"
            )
            evo = cur.fetchall()
            ecols = [d[0] for d in cur.description]
            if evo:
                lines.append(f"\nLast {len(evo)} evolution proposals:")
                for e in evo:
                    ed = dict(zip(ecols, e))
                    lines.append(
                        f"  {ed['created_at']}: "
                        f"{str(ed['title'])[:50]} → {ed['decision']} "
                        f"(delta={ed['delta_pct']:.1f}%, risk={ed['risk']})"
                    )
            conn.close()
            return "\n".join(lines)
        except Exception as exc:
            return f"Database query failed: {exc}"

    def search_web(query: str) -> str:
        """Search the web for research papers, techniques, and documentation
        about SAR image processing, vessel detection, object detection models,
        and maritime surveillance.

        Args:
            query: Search query, e.g. "SAR vessel detection YOLO fine-tuning",
                   "xView3 dataset best practices", "CFAR detector SAR"
        """
        import json as _json
        import urllib.parse
        import urllib.request

        # Try DuckDuckGo Instant Answer API
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1"
            req = urllib.request.Request(url, headers={"User-Agent": "SDW-Evolution/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
            lines = []
            if data.get("Abstract"):
                lines.append(f"Summary: {data['Abstract']}")
                if data.get("AbstractURL"):
                    lines.append(f"Source: {data['AbstractURL']}")
            for topic in data.get("RelatedTopics", [])[:5]:
                if isinstance(topic, dict) and topic.get("Text"):
                    lines.append(f"- {topic['Text'][:200]}")
                    if topic.get("FirstURL"):
                        lines.append(f"  URL: {topic['FirstURL']}")
            if lines:
                return "\n".join(lines)
        except Exception:
            pass

        # Try SearXNG if running locally
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"http://localhost:8888/search?q={encoded}&format=json"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
            results = data.get("results", [])[:5]
            lines = []
            for r in results:
                lines.append(f"- {r.get('title', 'N/A')}")
                lines.append(f"  {r.get('content', 'N/A')[:200]}")
            if lines:
                return "\n".join(lines)
        except Exception:
            pass

        return f"Web search returned no results for: {query}"

    def query_nautilus(intent: str, context: str) -> str:
        """Query data through the Nautilus data broker. Accesses configured
        data sources including PostGIS databases, AIS feeds, and geo data.

        Args:
            intent: What data to fetch, e.g. "detection-history", "ais-correlation",
                    "model-performance"
            context: JSON context for the query, e.g. '{"tile_id": "05bc615a", "limit": 10}'
        """
        import json as _json

        import psycopg

        from demos.sentinel_dark_watch.db import get_pg_dsn

        try:
            dsn = get_pg_dsn()
            conn = psycopg.connect(dsn)
            cur = conn.cursor()

            ctx = _json.loads(context) if context else {}

            if "detection" in intent.lower() or "history" in intent.lower():
                cur.execute(
                    "SELECT tile_id, COUNT(*) as n, AVG(confidence) as avg_conf"
                    " FROM detections GROUP BY tile_id ORDER BY n DESC LIMIT 10"
                )
            elif "ais" in intent.lower():
                cur.execute(
                    "SELECT mmsi, COUNT(*) as n FROM ais_positions"
                    " GROUP BY mmsi ORDER BY n DESC LIMIT 10"
                )
            elif "model" in intent.lower() or "performance" in intent.lower():
                cur.execute(
                    "SELECT version, map50, precision_val, recall_val,"
                    " training_samples, promoted, trained_at"
                    " FROM model_metrics ORDER BY trained_at DESC LIMIT 5"
                )
            else:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                )

            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            results = [dict(zip(cols, r)) for r in rows[:10]]
            conn.close()
            return _json.dumps(results, indent=2, default=str)[:2000]
        except Exception as exc:
            return f"Nautilus/DB query failed: {exc}"

    EVOLUTION_TOOLS = [query_pipeline_metrics, search_web, query_nautilus]

except ImportError:
    logger.info("DSPy not installed — signatures unavailable, nodes will use fallback templates")
    DSPY_AVAILABLE = False
    EVOLUTION_TOOLS = []
