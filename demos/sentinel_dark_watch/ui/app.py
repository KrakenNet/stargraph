"""Sentinel Dark Watch — Streamlit UI.

Four-tab interface: Live Map, Detection Review, Metrics Dashboard, Pipeline Status.
Communicates with Harbor serve API (REST + WebSocket) — no direct graph imports.
"""
from __future__ import annotations

import json
import os
from typing import Any

try:
    import streamlit as st
except ImportError:
    st = None  # type: ignore[assignment]

try:
    import folium
    from streamlit_folium import st_folium
except ImportError:
    folium = None  # type: ignore[assignment]
    st_folium = None  # type: ignore[assignment]

try:
    import plotly.graph_objects as go
except ImportError:
    go = None  # type: ignore[assignment]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HARBOR_URL = os.environ.get("HARBOR_URL", "http://localhost:9001")
POSTGRES_DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://harbor:harbor@localhost:5441/sdw",
)

# Strait of Hormuz center
MAP_CENTER_LAT = 26.5
MAP_CENTER_LON = 56.2
MAP_ZOOM = 8

RISK_COLORS = {
    "critical": "red",
    "high": "orange",
    "medium": "#cccc00",
    "low": "green",
}


# ---------------------------------------------------------------------------
# Harbor API helpers
# ---------------------------------------------------------------------------
def trigger_run(tile_ids: list[str]) -> dict[str, Any]:
    """POST /v1/runs — start a new pipeline run for given tile IDs."""
    if requests is None:
        return {"error": "requests not installed"}
    try:
        resp = requests.post(
            f"{HARBOR_URL}/v1/runs",
            json={
                "graph": "harbor.yaml",
                "initial_state": {"tile_queue": tile_ids},
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def get_detections(run_id: str) -> list[dict[str, Any]]:
    """Fetch detections from a run's state snapshot."""
    if requests is None:
        return []
    try:
        resp = requests.get(
            f"{HARBOR_URL}/v1/runs/{run_id}",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("state", {}).get("detections", [])
    except Exception:
        return []


def submit_review(
    run_id: str, corrections: list[dict[str, Any]]
) -> dict[str, Any]:
    """POST /v1/runs/{run_id}/respond — submit analyst corrections."""
    if requests is None:
        return {"error": "requests not installed"}
    try:
        resp = requests.post(
            f"{HARBOR_URL}/v1/runs/{run_id}/respond",
            json={"corrections": corrections},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def get_run_ids() -> list[str]:
    """Fetch recent run IDs from Harbor."""
    if requests is None:
        return []
    try:
        resp = requests.get(f"{HARBOR_URL}/v1/runs", timeout=10)
        resp.raise_for_status()
        runs = resp.json()
        if isinstance(runs, list):
            return [r.get("run_id", r.get("id", "")) for r in runs]
        return []
    except Exception:
        return []


def get_ais_tracks() -> list[dict[str, Any]]:
    """Fetch AIS track data for polyline rendering."""
    if requests is None:
        return []
    try:
        resp = requests.get(
            f"{HARBOR_URL}/v1/data/ais_tracks",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Postgres helpers (direct queries for metrics tabs)
# ---------------------------------------------------------------------------
def _pg_query(sql: str) -> list[dict[str, Any]]:
    """Run a read-only Postgres query. Returns list of row dicts."""
    try:
        import psycopg2
        import psycopg2.extras

        with psycopg2.connect(POSTGRES_DSN) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if st is None:
        print("Streamlit not installed — run: pip install streamlit")
        return

    st.set_page_config(
        page_title="Sentinel Dark Watch",
        page_icon="\U0001f6f0️",
        layout="wide",
    )
    st.title("Sentinel Dark Watch")
    st.caption("Maritime dark-vessel detection pipeline — powered by Harbor")

    tab_map, tab_review, tab_metrics, tab_pipeline = st.tabs(
        ["Live Map", "Detection Review", "Metrics Dashboard", "Pipeline Status"]
    )

    # ------------------------------------------------------------------
    # Tab 1: Live Map
    # ------------------------------------------------------------------
    with tab_map:
        _render_live_map()

    # ------------------------------------------------------------------
    # Tab 2: Detection Review (HITL)
    # ------------------------------------------------------------------
    with tab_review:
        _render_detection_review()

    # ------------------------------------------------------------------
    # Tab 3: Metrics Dashboard
    # ------------------------------------------------------------------
    with tab_metrics:
        _render_metrics_dashboard()

    # ------------------------------------------------------------------
    # Tab 4: Pipeline Status
    # ------------------------------------------------------------------
    with tab_pipeline:
        _render_pipeline_status()


def _render_metrics_dashboard() -> None:
    """Metrics Dashboard — Plotly charts from run_metrics + model_metrics (AC-10.x)."""
    st.subheader("Metrics Dashboard")

    if go is None:
        st.warning("Plotly not installed — charts unavailable. Install: pip install plotly")
        return

    # Fetch metrics from Postgres
    run_metrics = _pg_query(
        "SELECT * FROM run_metrics ORDER BY created_at DESC LIMIT 50"
    )
    model_metrics = _pg_query(
        "SELECT * FROM model_metrics ORDER BY trained_at DESC LIMIT 20"
    )

    if not run_metrics and not model_metrics:
        st.info("No metrics data available yet. Run the pipeline to generate metrics.")
        return

    # --- mAP over model versions (line chart, AC-10.1) ---
    if model_metrics:
        st.markdown("#### Model Performance (mAP)")
        versions = [m.get("version", "") for m in model_metrics]
        map50_vals = [m.get("map50", 0) for m in model_metrics]
        map50_95_vals = [m.get("map50_95", 0) for m in model_metrics]

        fig_map = go.Figure()
        fig_map.add_trace(go.Scatter(
            x=versions, y=map50_vals,
            mode="lines+markers", name="mAP@50",
            line=dict(color="blue"),
        ))
        fig_map.add_trace(go.Scatter(
            x=versions, y=map50_95_vals,
            mode="lines+markers", name="mAP@50-95",
            line=dict(color="orange", dash="dash"),
        ))
        fig_map.update_layout(
            xaxis_title="Model Version",
            yaxis_title="mAP Score",
            yaxis=dict(range=[0, 1]),
            height=350,
        )
        st.plotly_chart(fig_map, use_container_width=True)

        # Before/after comparison card (when retrained model exists)
        if len(model_metrics) >= 2:
            latest = model_metrics[0]
            previous = model_metrics[1]
            st.markdown("#### Champion vs Challenger")
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                delta_map = latest.get("map50", 0) - previous.get("map50", 0)
                st.metric(
                    f"mAP@50 ({latest.get('version', '?')})",
                    f"{latest.get('map50', 0):.3f}",
                    delta=f"{delta_map:+.3f}",
                )
            with cc2:
                st.metric(
                    "Training Samples",
                    latest.get("training_samples", 0),
                    delta=latest.get("training_samples", 0) - previous.get("training_samples", 0),
                )
            with cc3:
                st.metric(
                    "Precision",
                    f"{latest.get('precision', 0):.3f}",
                    delta=f"{latest.get('precision', 0) - previous.get('precision', 0):+.3f}",
                )

    # --- Dark vessel count per run (bar chart, AC-10.2) ---
    if run_metrics:
        st.markdown("#### Dark Vessels per Run")
        run_ids = [r.get("run_id", f"run_{i}") for i, r in enumerate(run_metrics)]
        dark_counts = [r.get("dark_vessels_flagged", 0) for r in run_metrics]

        fig_dark = go.Figure()
        fig_dark.add_trace(go.Bar(
            x=run_ids, y=dark_counts,
            marker_color="crimson",
            name="Dark Vessels",
        ))
        fig_dark.update_layout(
            xaxis_title="Run ID",
            yaxis_title="Dark Vessel Count",
            height=300,
        )
        st.plotly_chart(fig_dark, use_container_width=True)

    # --- FP rate trend (line chart) ---
    if run_metrics:
        st.markdown("#### False Positive Rate Trend")
        fp_rates = []
        run_labels = []
        for i, r in enumerate(run_metrics):
            total = r.get("total_detections", 0)
            fp = r.get("false_positives_rejected", 0)
            rate = (fp / total * 100) if total > 0 else 0
            fp_rates.append(rate)
            run_labels.append(r.get("run_id", f"run_{i}"))

        fig_fp = go.Figure()
        fig_fp.add_trace(go.Scatter(
            x=run_labels, y=fp_rates,
            mode="lines+markers", name="FP Rate (%)",
            line=dict(color="orange"),
            fill="tozeroy",
        ))
        fig_fp.update_layout(
            xaxis_title="Run ID",
            yaxis_title="False Positive Rate (%)",
            yaxis=dict(range=[0, 100]),
            height=300,
        )
        st.plotly_chart(fig_fp, use_container_width=True)

    # --- Tiles/hour gauge ---
    if run_metrics:
        st.markdown("#### Processing Throughput")
        latest_run = run_metrics[0]
        tiles = latest_run.get("tiles_processed", 0)
        secs = latest_run.get("processing_time_seconds", 1)
        tiles_per_hour = (tiles / secs * 3600) if secs > 0 else 0

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=tiles_per_hour,
            title={"text": "Tiles / Hour (latest run)"},
            gauge={
                "axis": {"range": [0, max(tiles_per_hour * 2, 100)]},
                "bar": {"color": "darkblue"},
                "steps": [
                    {"range": [0, tiles_per_hour * 0.5], "color": "lightgray"},
                    {"range": [tiles_per_hour * 0.5, tiles_per_hour * 1.5], "color": "lightskyblue"},
                ],
            },
        ))
        fig_gauge.update_layout(height=250)
        st.plotly_chart(fig_gauge, use_container_width=True)


def _render_pipeline_status() -> None:
    """Pipeline Status tab — live node progress + JSONL audit viewer (AC-11.x)."""
    st.subheader("Pipeline Status")

    run_id = st.text_input("Run ID", placeholder="Enter run ID", key="pipeline_run_id")
    if not run_id:
        st.info("Enter a run ID to view pipeline status.")
        return

    col_status, col_log = st.columns([1, 1])

    with col_status:
        st.markdown("#### Node Progress")

        # Fetch run state for current node + pipeline phase
        if requests is not None:
            try:
                resp = requests.get(f"{HARBOR_URL}/v1/runs/{run_id}", timeout=10)
                resp.raise_for_status()
                run_data = resp.json()
                state = run_data.get("state", {})
                status = run_data.get("status", "unknown")

                st.markdown(f"**Status:** `{status}`")
                st.markdown(f"**Phase:** `{state.get('pipeline_phase', 'unknown')}`")
                st.markdown(f"**Model version:** `{state.get('model_version', '?')}`")

                if state.get("last_error"):
                    st.error(f"Error: {state['last_error']}")

                # Pipeline nodes and progress
                pipeline_nodes = [
                    "sar_ingest", "yolo_inference", "nms_dedup", "land_mask",
                    "ais_correlation", "geo_context", "risk_scoring", "reporting",
                    "emit_sar_chips", "analyst_review", "metrics_collector",
                    "retrain_trigger", "action_done",
                ]
                phase = state.get("pipeline_phase", "ingest")

                # Estimate progress from pipeline phase
                phase_map = {
                    "ingest": 1, "inference": 2, "nms": 3, "land_mask": 4,
                    "ais_correlation": 5, "geo_context": 6, "risk_scoring": 7,
                    "reporting": 8, "chips": 9, "review": 10, "metrics": 11,
                    "retrain": 12, "done": 13,
                }
                current_step = phase_map.get(phase, 0)
                total_steps = len(pipeline_nodes)
                progress = min(current_step / total_steps, 1.0) if total_steps > 0 else 0

                st.progress(progress, text=f"Step {current_step}/{total_steps}")

                # Per-node timing (if available in events)
                events = run_data.get("events", [])
                if events:
                    st.markdown("**Node Timing:**")
                    for evt in events[-15:]:  # last 15 events
                        node_id = evt.get("node_id", "?")
                        dur = evt.get("duration_ms", 0)
                        evt_type = evt.get("type", "?")
                        st.text(f"  {node_id}: {dur}ms ({evt_type})")

            except Exception as exc:
                st.error(f"Failed to fetch run data: {exc}")
        else:
            st.warning("requests not installed")

    with col_log:
        st.markdown("#### Audit Log (JSONL)")

        # WebSocket streaming not available in Streamlit natively;
        # fall back to polling the audit log endpoint
        if requests is not None:
            try:
                resp = requests.get(
                    f"{HARBOR_URL}/v1/runs/{run_id}/events",
                    timeout=10,
                )
                resp.raise_for_status()
                events = resp.json()

                if not events:
                    st.info("No audit events yet.")
                else:
                    # Render as scrollable log
                    log_lines = []
                    for evt in events:
                        ts = evt.get("ts", evt.get("timestamp", ""))
                        node = evt.get("node_id", "")
                        etype = evt.get("type", evt.get("event", ""))
                        dur = evt.get("duration_ms", "")
                        line = f"[{ts}] {node}: {etype}"
                        if dur:
                            line += f" ({dur}ms)"
                        log_lines.append(line)

                    st.text_area(
                        "Event Stream",
                        value="\n".join(log_lines),
                        height=400,
                        disabled=True,
                        key="audit_log_area",
                    )

                    # Download JSONL button
                    jsonl_data = "\n".join(json.dumps(e) for e in events)
                    st.download_button(
                        "Download JSONL",
                        data=jsonl_data,
                        file_name=f"audit_{run_id}.jsonl",
                        mime="application/jsonl",
                    )
            except Exception as exc:
                st.error(f"Failed to fetch events: {exc}")
        else:
            st.warning("requests not installed")

        # Auto-refresh toggle
        auto_refresh = st.checkbox("Auto-refresh (5s)", key="auto_refresh")
        if auto_refresh:
            st.markdown("*Page will rerun every 5 seconds.*")
            import time
            time.sleep(5)
            st.rerun()


def _render_detection_review() -> None:
    """Detection Review tab — analyst HITL review of detections (AC-6.3, AC-7.4, AC-8.x)."""
    st.subheader("Detection Review")

    run_id = st.text_input("Run ID", placeholder="Enter run ID", key="review_run_id")
    if not run_id:
        st.info("Enter a run ID to load detections for review.")
        return

    detections = get_detections(run_id)
    if not detections:
        st.warning("No detections found for this run.")
        return

    # Sort by risk score descending (AC-6.3)
    detections.sort(key=lambda d: d.get("risk_score", 0), reverse=True)

    st.markdown(f"**{len(detections)} detections** — sorted by risk score (highest first)")

    # Track corrections for batch submission
    if "corrections" not in st.session_state:
        st.session_state.corrections = {}

    for idx, det in enumerate(detections):
        det_id = det.get("detection_id", f"det_{idx}")
        risk = det.get("risk_level", "low").lower()
        risk_color = RISK_COLORS.get(risk, "gray")

        with st.expander(
            f"{det_id} | Risk: {risk.upper()} ({det.get('risk_score', 0)}) "
            f"| Conf: {det.get('confidence', 0):.2f}",
            expanded=(idx < 3),
        ):
            col_img, col_details = st.columns([1, 2])

            # SAR chip image
            with col_img:
                chip_ref = det.get("chip_artifact_ref", "")
                if chip_ref:
                    st.image(
                        f"{HARBOR_URL}/v1/artifacts/{chip_ref}",
                        caption=f"SAR Chip — {det_id}",
                        use_container_width=True,
                    )
                else:
                    st.markdown("*No SAR chip available*")

            with col_details:
                # Confidence bar
                confidence = det.get("confidence", 0)
                st.progress(min(confidence, 1.0), text=f"Confidence: {confidence:.1%}")

                # AIS status badge
                if det.get("dark_vessel", False):
                    st.markdown(":red[**DARK VESSEL** — No AIS correlation]")
                else:
                    mmsi = det.get("ais_mmsi", "unknown")
                    name = det.get("ais_vessel_name", "unknown")
                    flag = det.get("ais_flag_state", "unknown")
                    vtype = det.get("ais_vessel_type", "unknown")
                    st.markdown(
                        f":green[**AIS MATCHED**] — {name} (MMSI: {mmsi}, "
                        f"Flag: {flag}, Type: {vtype})"
                    )

                # Geo-summary
                st.markdown(
                    f"**Location:** {det.get('geo_lat', 0):.4f}, {det.get('geo_lon', 0):.4f} | "
                    f"**EEZ:** {det.get('eez_name', 'unknown')} | "
                    f"**Port dist:** {det.get('distance_to_port_nm', 0):.1f} nm | "
                    f"**Coast dist:** {det.get('distance_to_coast_nm', 0):.1f} nm"
                )
                if det.get("fishing_zone"):
                    st.markdown(":orange[Fishing zone]")

                # Risk level badge
                st.markdown(
                    f"**Risk Level:** :{risk_color}[**{risk.upper()}**] "
                    f"(score: {det.get('risk_score', 0)})"
                )

            # Draft report text — editable (AC-7.4)
            report_key = f"report_{det_id}"
            edited_report = st.text_area(
                "Draft Report",
                value=det.get("report_text", ""),
                height=120,
                key=report_key,
                help="Edit the draft report before submission.",
            )

            # Action buttons
            bcol1, bcol2, bcol3, bcol4 = st.columns(4)
            with bcol1:
                if st.button("Confirm Vessel", key=f"confirm_{det_id}"):
                    st.session_state.corrections[det_id] = {
                        "detection_id": det_id,
                        "decision": "confirm",
                        "report_text": edited_report,
                    }
                    st.success("Marked: Confirm")
            with bcol2:
                if st.button("Reject (FP)", key=f"reject_{det_id}"):
                    st.session_state.corrections[det_id] = {
                        "detection_id": det_id,
                        "decision": "reject",
                        "report_text": edited_report,
                    }
                    st.warning("Marked: Reject (False Positive)")
            with bcol3:
                if st.button("Flag for Review", key=f"flag_{det_id}"):
                    st.session_state.corrections[det_id] = {
                        "detection_id": det_id,
                        "decision": "flag",
                        "report_text": edited_report,
                    }
                    st.info("Marked: Flag for Review")
            with bcol4:
                override_risk = st.selectbox(
                    "Override Risk",
                    options=["", "critical", "high", "medium", "low"],
                    key=f"override_{det_id}",
                )
                if override_risk:
                    if det_id not in st.session_state.corrections:
                        st.session_state.corrections[det_id] = {
                            "detection_id": det_id,
                            "decision": "confirm",
                            "report_text": edited_report,
                        }
                    st.session_state.corrections[det_id]["override_risk"] = override_risk

    # Batch submission
    st.divider()
    corrections_list = list(st.session_state.corrections.values())
    st.markdown(f"**{len(corrections_list)} corrections** pending submission")

    if st.button("Submit All Corrections", type="primary", disabled=len(corrections_list) == 0):
        result = submit_review(run_id, corrections_list)
        if "error" in result:
            st.error(f"Submission failed: {result['error']}")
        else:
            st.success("Corrections submitted successfully.")
            st.session_state.corrections = {}
            st.rerun()


def _render_live_map() -> None:
    """Live Map tab — Folium map with detection markers + AIS tracks."""
    st.subheader("Live Detection Map")

    # Sidebar controls for triggering a new run
    with st.sidebar:
        st.markdown("### Run Controls")
        tile_input = st.text_input(
            "Tile IDs (comma-separated)",
            placeholder="tile_001, tile_002",
        )
        if st.button("Trigger Pipeline Run"):
            if tile_input.strip():
                tile_ids = [t.strip() for t in tile_input.split(",") if t.strip()]
                result = trigger_run(tile_ids)
                if "error" in result:
                    st.error(f"Failed: {result['error']}")
                else:
                    st.success(f"Run started: {result.get('run_id', 'unknown')}")
            else:
                st.warning("Enter at least one tile ID")

    # Run selector
    col_run, col_refresh = st.columns([3, 1])
    with col_run:
        run_id = st.text_input("Run ID", placeholder="Enter run ID to view detections")
    with col_refresh:
        st.write("")  # spacer
        refresh = st.button("Refresh", key="map_refresh")

    # Fetch detections
    detections: list[dict[str, Any]] = []
    if run_id:
        detections = get_detections(run_id)

    if folium is None or st_folium is None:
        st.warning("Folium not installed — map view unavailable. Install: pip install folium streamlit-folium")
        if detections:
            st.json(detections[:5])
        return

    # Build map
    m = folium.Map(
        location=[MAP_CENTER_LAT, MAP_CENTER_LON],
        zoom_start=MAP_ZOOM,
        tiles="CartoDB positron",
    )

    # Detection markers color-coded by risk level
    for det in detections:
        risk = det.get("risk_level", "low").lower()
        color = RISK_COLORS.get(risk, "gray")
        lat = det.get("geo_lat", 0)
        lon = det.get("geo_lon", 0)
        if lat == 0 and lon == 0:
            continue

        popup_html = (
            f"<b>ID:</b> {det.get('detection_id', '?')}<br>"
            f"<b>Risk:</b> {risk.upper()} ({det.get('risk_score', 0)})<br>"
            f"<b>Confidence:</b> {det.get('confidence', 0):.2f}<br>"
            f"<b>Dark vessel:</b> {det.get('dark_vessel', False)}<br>"
            f"<b>EEZ:</b> {det.get('eez_name', 'unknown')}<br>"
            f"<b>Vessel:</b> {det.get('ais_vessel_name', 'unidentified')}"
        )

        folium.CircleMarker(
            location=[lat, lon],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{det.get('detection_id', '?')} — {risk.upper()}",
        ).add_to(m)

    # AIS tracks as polylines
    ais_tracks = get_ais_tracks()
    for track in ais_tracks:
        positions = track.get("positions", [])
        if len(positions) < 2:
            continue
        coords = [[p.get("lat", 0), p.get("lon", 0)] for p in positions]
        folium.PolyLine(
            coords,
            color="blue",
            weight=2,
            opacity=0.6,
            tooltip=track.get("mmsi", "unknown"),
        ).add_to(m)

    st_folium(m, width=None, height=600, use_container_width=True)

    # Detection summary below map
    if detections:
        st.markdown(f"**{len(detections)} detections** on map")
        risk_counts = {}
        for d in detections:
            rl = d.get("risk_level", "low")
            risk_counts[rl] = risk_counts.get(rl, 0) + 1
        cols = st.columns(4)
        for i, level in enumerate(["critical", "high", "medium", "low"]):
            with cols[i]:
                st.metric(level.upper(), risk_counts.get(level, 0))


if __name__ == "__main__":
    main()
