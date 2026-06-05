# SPDX-License-Identifier: Apache-2.0
"""Shared PostGIS spatial query helpers for Sentinel Dark Watch.

All functions take an asyncpg connection pool or connection as the
first argument and return typed results.  Nodes should import these
instead of embedding inline SQL.
"""

from __future__ import annotations

import math
from typing import Any


async def point_in_eez(
    pool: Any,
    lat: float,
    lon: float,
) -> str | None:
    """Return the EEZ name containing (*lon*, *lat*), or ``None``."""
    row = await pool.fetchrow(
        "SELECT geoname FROM eez_boundaries"
        " WHERE ST_Contains("
        "   geom, ST_SetSRID(ST_MakePoint($1, $2), 4326)"
        " ) LIMIT 1",
        lon,
        lat,
    )
    return row["geoname"] if row else None


async def nearest_port(
    pool: Any,
    lat: float,
    lon: float,
) -> tuple[str | None, float]:
    """Return ``(port_name, distance_m)`` for the closest port.

    Returns ``(None, 0.0)`` if no ports exist.
    """
    row = await pool.fetchrow(
        "SELECT port_name,"
        "  ST_Distance("
        "    geom::geography,"
        "    ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography"
        "  ) AS dist_m"
        " FROM ports ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)"
        " LIMIT 1",
        lon,
        lat,
    )
    if row:
        return row["port_name"], float(row["dist_m"])
    return None, 0.0


async def point_on_land(
    pool: Any,
    lat: float,
    lon: float,
) -> bool:
    """Return ``True`` if (*lon*, *lat*) falls inside any coastline polygon."""
    return await pool.fetchval(
        "SELECT EXISTS("
        "  SELECT 1 FROM coastlines"
        "  WHERE ST_Contains("
        "    geom,"
        "    ST_SetSRID(ST_MakePoint($1, $2), 4326)"
        "  )"
        ")",
        lon,
        lat,
    )


async def nearest_coast_distance_m(
    pool: Any,
    lat: float,
    lon: float,
) -> float | None:
    """Return distance in metres to the nearest coastline, or ``None``."""
    val = await pool.fetchval(
        "SELECT ST_Distance("
        "  geom::geography,"
        "  ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography"
        ") FROM coastlines"
        " ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)"
        " LIMIT 1",
        lon,
        lat,
    )
    return float(val) if val is not None else None


def predicted_ais_position(
    lat: float,
    lon: float,
    speed_kn: float,
    heading_deg: float,
    time_delta_hours: float,
) -> tuple[float, float]:
    """Predict vessel position from last AIS report.

    Simple dead-reckoning: lat += speed * cos(heading) * dt / 60,
    lon += speed * sin(heading) * dt / 60.

    Returns ``(predicted_lat, predicted_lon)``.
    """
    heading_rad = math.radians(heading_deg)
    predicted_lat = lat + speed_kn * math.cos(heading_rad) * time_delta_hours / 60.0
    predicted_lon = lon + speed_kn * math.sin(heading_rad) * time_delta_hours / 60.0
    return predicted_lat, predicted_lon
