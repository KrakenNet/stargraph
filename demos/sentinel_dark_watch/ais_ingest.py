"""AIS ingest daemon — mock bulk-load or live WebSocket stream to Postgres."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib

log = logging.getLogger(__name__)

_HERE = pathlib.Path(__file__).resolve().parent
_FIXTURE = _HERE / "fixtures" / "ais_positions.json"

_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds


# ---------------------------------------------------------------------------
# Mock mode — load fixtures, bulk-insert, exit
# ---------------------------------------------------------------------------

async def _mock_ingest(dsn: str) -> None:
    """Load fixture AIS data and bulk-insert into ais_positions table."""
    try:
        import asyncpg  # noqa: F811
    except ImportError:
        log.error("asyncpg required for mock ingest — pip install asyncpg")
        return

    data = json.loads(_FIXTURE.read_text())
    log.info("Loaded %d AIS positions from fixture", len(data))

    conn = await asyncpg.connect(dsn)
    try:
        await conn.executemany(
            """
            INSERT INTO ais_positions (mmsi, lat, lon, speed_kn, heading, ts, ship_name, flag_state, vessel_type)
            VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7, $8, $9)
            ON CONFLICT DO NOTHING
            """,
            [
                (
                    r["mmsi"],
                    r["lat"],
                    r["lon"],
                    r["speed_kn"],
                    r["heading"],
                    r["ts"],
                    r.get("ship_name", ""),
                    r.get("flag_state", ""),
                    r.get("vessel_type", ""),
                )
                for r in data
            ],
        )
        log.info("Inserted %d AIS positions (mock mode)", len(data))
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Live mode — AISStream.io WebSocket
# ---------------------------------------------------------------------------

async def _live_ingest(dsn: str, api_key: str) -> None:
    """Connect to AISStream.io WebSocket, parse PositionReport, write to PG."""
    try:
        import asyncpg
        import websockets  # type: ignore[import-untyped]
    except ImportError as exc:
        log.error("Missing dependency for live mode: %s", exc)
        return

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)

    subscribe_msg = json.dumps({
        "APIKey": api_key,
        "BoundingBoxes": [
            [[25.0, 54.0], [27.5, 57.5]],  # Strait of Hormuz AOI
        ],
        "FiltersShipMMSI": [],
        "FilterMessageTypes": ["PositionReport"],
    })

    retries = 0
    while retries < _MAX_RETRIES:
        try:
            async with websockets.connect(
                "wss://stream.aisstream.io/v0/stream"
            ) as ws:
                await ws.send(subscribe_msg)
                log.info("Connected to AISStream.io (attempt %d)", retries + 1)
                retries = 0  # reset on successful connect

                async for raw in ws:
                    msg = json.loads(raw)
                    meta = msg.get("MetaData", {})
                    pos = msg.get("Message", {}).get("PositionReport", {})
                    if not pos:
                        continue

                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            INSERT INTO ais_positions
                                (mmsi, lat, lon, speed_kn, heading, ts, ship_name, flag_state, vessel_type)
                            VALUES ($1, $2, $3, $4, $5, now(), $6, '', '')
                            ON CONFLICT DO NOTHING
                            """,
                            str(meta.get("MMSI", "")),
                            pos.get("Latitude", 0.0),
                            pos.get("Longitude", 0.0),
                            pos.get("Sog", 0.0),      # speed over ground
                            pos.get("TrueHeading", 0),
                            meta.get("ShipName", ""),
                        )
        except (ConnectionError, OSError) as exc:
            retries += 1
            delay = _BACKOFF_BASE ** retries
            log.warning("WebSocket disconnected (%s), retry %d/%d in %ds",
                        exc, retries, _MAX_RETRIES, delay)
            await asyncio.sleep(delay)
        except Exception:
            log.exception("Unexpected error in live ingest")
            retries += 1
            delay = _BACKOFF_BASE ** retries
            await asyncio.sleep(delay)

    log.error("Exhausted %d retries — exiting live ingest", _MAX_RETRIES)
    await pool.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    dsn = os.environ.get(
        "POSTGRES_DSN",
        "postgresql://harbor:harbor@localhost:5441/sdw",
    )
    mode = os.environ.get("AIS_MODE", "mock").lower()

    if mode == "mock":
        log.info("AIS ingest — mock mode")
        await _mock_ingest(dsn)
    else:
        api_key = os.environ.get("AIS_STREAM_API_KEY", "")
        if not api_key:
            log.error("AIS_STREAM_API_KEY required for live mode")
            return
        log.info("AIS ingest — live mode")
        await _live_ingest(dsn, api_key)


if __name__ == "__main__":
    asyncio.run(main())
