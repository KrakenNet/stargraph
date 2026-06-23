# SPDX-License-Identifier: Apache-2.0
"""sentinel_dark_watch demo bootstrap.

One-shot provisioning step. Idempotent — safe to re-run.

Phases:
  1. Wait for docker-compose services healthy (postgis, redis, llm-shim).
  2. Provision PostGIS extension + all DDL (sar_tiles, ais_positions,
     eez_boundaries, ports, coastlines, detections, corrections,
     run_metrics, model_metrics).
  3. Seed fixture AIS positions from fixtures/ais_positions.json.
  4. Seed EEZ boundaries (Iranian + Omani EEZ), ports (5 near AOI),
     and simplified coastline polygon for the Strait of Hormuz.

Usage::

    cp demos/sentinel_dark_watch/.env.example demos/sentinel_dark_watch/.env
    docker compose -f demos/sentinel_dark_watch/docker-compose.yml up -d
    uv run --no-project python -m demos.sentinel_dark_watch.bootstrap
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader (no external dep — keeps bootstrap stand-alone)
# ---------------------------------------------------------------------------


def _load_env(env_path: Path) -> None:
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_DEMO_DIR = Path(__file__).resolve().parent
_load_env(_DEMO_DIR / ".env")
_load_env(_DEMO_DIR / ".env.example")  # fallback for unset keys


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------


def _wait_tcp(host: str, port: int, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=1):
            return
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {host}:{port}")


def _wait_http_health(url: str, timeout_seconds: int = 60) -> None:
    """Try *url* first; if it 404s, fall back to the root (ollama compat)."""
    base = url.rsplit("/health", 1)[0] or url
    urls = [url, base + "/", base]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for u in urls:
            with contextlib.suppress(Exception), urllib.request.urlopen(u, timeout=2) as resp:
                if resp.status == 200:
                    return
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {url}")


def _wait_services() -> None:
    print("[1/4] Waiting for docker-compose services…")
    _wait_tcp("localhost", int(os.environ["POSTGRES_PORT"]))
    print("      postgis  OK")
    _wait_tcp("localhost", int(os.environ["REDIS_PORT"]))
    print("      redis    OK")
    _wait_http_health(
        os.environ["LLM_BASE_URL"].rstrip("/").rsplit("/v1", 1)[0] + "/health",
    )
    print("      llm-shim OK")


# ---------------------------------------------------------------------------
# DDL — PostGIS extension + all tables + GIST indexes
# ---------------------------------------------------------------------------

_SCHEMA_SQL = _DEMO_DIR / "schema.sql"


def _provision_postgis() -> None:
    print("[2/4] Provisioning PostGIS schemas…")
    import psycopg

    ddl = _SCHEMA_SQL.read_text(encoding="utf-8")
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(ddl)


# ---------------------------------------------------------------------------
# Seed AIS fixture data
# ---------------------------------------------------------------------------


def _seed_ais_positions() -> None:
    print("[3/4] Seeding AIS fixture positions…")
    import psycopg

    fixture_path = _DEMO_DIR / "fixtures" / "ais_positions.json"
    positions = json.loads(fixture_path.read_text(encoding="utf-8"))

    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        for pos in positions:
            cur.execute(
                """
                INSERT INTO ais_positions (mmsi, lat, lon, speed_kn, heading, ts,
                                           ship_name, flag_state, vessel_type)
                VALUES (%(mmsi)s, %(lat)s, %(lon)s, %(speed_kn)s, %(heading)s,
                        %(ts)s, %(ship_name)s, %(flag_state)s, %(vessel_type)s)
                ON CONFLICT DO NOTHING
                """,
                pos,
            )
    print(f"      seeded {len(positions)} AIS positions")


# ---------------------------------------------------------------------------
# Seed geo fixtures — EEZ, ports, coastline
# ---------------------------------------------------------------------------

# Simplified EEZ polygons (Strait of Hormuz area)
_EEZ_FIXTURES = [
    {
        "mrgid": 8377,
        "geoname": "Iranian Exclusive Economic Zone",
        "sovereign": "Iran",
        "wkt": (
            "MULTIPOLYGON((("
            "54.0 25.5, 54.0 27.5, 57.0 27.5, 57.0 26.5, "
            "56.5 26.0, 56.0 25.5, 54.0 25.5"
            ")))"
        ),
    },
    {
        "mrgid": 8382,
        "geoname": "Omani Exclusive Economic Zone",
        "sovereign": "Oman",
        "wkt": (
            "MULTIPOLYGON(((56.0 24.0, 56.0 26.0, 57.5 26.0, 58.0 25.0, 57.5 24.0, 56.0 24.0)))"
        ),
    },
    {
        "mrgid": 8371,
        "geoname": "UAE Exclusive Economic Zone",
        "sovereign": "United Arab Emirates",
        "wkt": (
            "MULTIPOLYGON(((54.0 24.0, 54.0 25.5, 56.0 25.5, 56.0 24.5, 55.5 24.0, 54.0 24.0)))"
        ),
    },
]

# Ports near Strait of Hormuz AOI
_PORT_FIXTURES = [
    {"port_name": "Bandar Abbas", "country": "Iran", "lat": 27.1832, "lon": 56.2666},
    {"port_name": "Fujairah", "country": "UAE", "lat": 25.1288, "lon": 56.3264},
    {"port_name": "Muscat", "country": "Oman", "lat": 23.6100, "lon": 58.5400},
    {"port_name": "Khasab", "country": "Oman", "lat": 26.1800, "lon": 56.2500},
    {"port_name": "Jask", "country": "Iran", "lat": 25.6400, "lon": 57.7700},
    {"port_name": "Sohar", "country": "Oman", "lat": 24.3600, "lon": 56.7300},
    {"port_name": "Ras Al Khaimah", "country": "UAE", "lat": 25.7953, "lon": 55.9432},
    {"port_name": "Chabahar", "country": "Iran", "lat": 25.2919, "lon": 60.6430},
]

# Simplified coastline polygon covering Iranian / Omani coast near Strait
_COASTLINE_WKT = (
    "MULTIPOLYGON((("
    "54.0 25.4, 54.5 25.6, 55.0 25.8, 55.5 26.0, "
    "56.0 26.2, 56.3 26.5, 56.5 26.8, 57.0 27.0, "
    "57.5 26.5, 57.0 26.0, 56.5 25.5, 56.0 25.0, "
    "55.5 24.5, 55.0 24.3, 54.5 24.5, 54.0 25.0, "
    "54.0 25.4"
    ")))"
)


def _seed_geo_fixtures() -> None:
    print("[4/4] Seeding EEZ, port, and coastline fixtures…")
    import psycopg

    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        # EEZ boundaries
        for eez in _EEZ_FIXTURES:
            cur.execute(
                """
                INSERT INTO eez_boundaries (mrgid, geoname, sovereign, geom)
                VALUES (%(mrgid)s, %(geoname)s, %(sovereign)s,
                        ST_GeomFromText(%(wkt)s, 4326))
                ON CONFLICT DO NOTHING
                """,
                eez,
            )
        print(f"      seeded {len(_EEZ_FIXTURES)} EEZ boundaries")

        # Ports
        for port in _PORT_FIXTURES:
            cur.execute(
                """
                INSERT INTO ports (port_name, country, lat, lon)
                VALUES (%(port_name)s, %(country)s, %(lat)s, %(lon)s)
                ON CONFLICT DO NOTHING
                """,
                port,
            )
        print(f"      seeded {len(_PORT_FIXTURES)} ports")

        # Coastline
        cur.execute(
            """
            INSERT INTO coastlines (gid, geom)
            VALUES (1, ST_GeomFromText(%s, 4326))
            ON CONFLICT DO NOTHING
            """,
            (_COASTLINE_WKT,),
        )
        print("      seeded 1 coastline polygon")


# ---------------------------------------------------------------------------
# Seed SAR tiles from xView3 imagery
# ---------------------------------------------------------------------------


def _seed_sar_tiles() -> None:
    imagery_dir = _DEMO_DIR / "data" / "xview3" / "imagery"
    if not imagery_dir.exists():
        print("[5/5] Skipping sar_tiles — no xView3 imagery downloaded")
        return

    print("[5/5] Seeding sar_tiles from xView3 scenes…")
    import psycopg

    try:
        import rasterio
        from rasterio.warp import transform_bounds
    except ImportError:
        print("      rasterio not installed — skipping sar_tiles seeding")
        return

    scenes = sorted(p for p in imagery_dir.iterdir() if p.is_dir() and (p / "VH_dB.tif").exists())
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as conn, conn.cursor() as cur:
        for scene_dir in scenes:
            scene_id = scene_dir.name
            vh_path = scene_dir / "VH_dB.tif"

            with rasterio.open(vh_path) as src:
                west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)

            bounds_wkt = (
                f"POLYGON(({west} {south}, {east} {south}, "
                f"{east} {north}, {west} {north}, {west} {south}))"
            )
            cur.execute(
                """
                INSERT INTO sar_tiles (tile_id, scene_id, file_path, acquired_at, bounds)
                VALUES (%s, %s, %s, NOW(), ST_GeomFromText(%s, 4326))
                ON CONFLICT (tile_id) DO NOTHING
                """,
                (scene_id, scene_id, str(scene_dir), bounds_wkt),
            )
    print(f"      seeded {len(scenes)} SAR tiles")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _ensure_data_dirs() -> None:
    """Create data/ sub-directories (audit log, etc.) if they don't exist."""
    (_DEMO_DIR / "data" / "audit").mkdir(parents=True, exist_ok=True)


def main() -> int:
    print(f"sentinel_dark_watch bootstrap  ({_DEMO_DIR})")
    _ensure_data_dirs()
    _wait_services()
    _provision_postgis()
    _seed_ais_positions()
    _seed_geo_fixtures()
    _seed_sar_tiles()
    print("Bootstrap complete. Next: run `python -m demos.sentinel_dark_watch.serve_sdw`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
