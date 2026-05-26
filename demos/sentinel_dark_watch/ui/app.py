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
