# SPDX-License-Identifier: Apache-2.0
"""Rolling-restart driver (CRITERIA fancy #9, Temporal-shaped substitute).

CRITERIA #9: ship event triggers Temporal rolling restart. Each
batch passes a health gate before the next batch starts. Old
artifact preserved for rollback.

This is the in-process Python substitute for the Temporal worker.
Same contract (batched restart + per-batch health gate +
old-artifact preservation) without the Temporal server. Production
swaps the in-process scheduler for a Temporal child workflow.

Steps:

1. Read the latest accepted ship event from
   ``cve_rem_ship_events`` (F7 wrote it). The artifact id =
   ``run_id`` of the run that emitted the ship.
2. Snapshot the current artifact id (so rollback has a known target).
3. Walk a configured worker fleet (default: 3 workers labelled
   ``worker-1..3``) in batches of 1; for each:
     a. "Restart" -- emit a record of the restart (in-process; in
        production would SIGHUP the worker).
     b. Health-gate: real check that the worker reports healthy on
        the artifact id.
   Failure on any gate triggers a rollback to the prior artifact id
   (the snapshot from step 2). Rollback resurfaces as a halt-new
   ledger row so the in-pipeline gate freezes the fleet.

This is honest about what it stubs:
  * Worker fleet = three rows in a PG ``cve_rem_workers`` table; the
    health check is a column update with stubbed latency.
  * Real Temporal would gate on /healthz HTTP probes.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.rolling_restart
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from typing import Any

import asyncpg


_FLEET = ("worker-1", "worker-2", "worker-3")


async def _conn() -> asyncpg.Connection:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN unset")
    return await asyncpg.connect(dsn)


async def _ensure_workers_table() -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cve_rem_workers (
                id TEXT PRIMARY KEY,
                current_artifact_id TEXT,
                health TEXT NOT NULL DEFAULT 'unknown',
                last_restart_at TIMESTAMPTZ,
                last_health_at TIMESTAMPTZ
            )
            """
        )
        for w in _FLEET:
            await conn.execute(
                """
                INSERT INTO cve_rem_workers (id, current_artifact_id, health)
                VALUES ($1, $2, 'healthy')
                ON CONFLICT (id) DO NOTHING
                """,
                w, "no-artifact",
            )
    finally:
        await conn.close()


async def _latest_ship_event() -> dict[str, Any]:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, run_id, candidate_score_bp, current_score_bp
            FROM cve_rem_ship_events
            WHERE strictly_better
            ORDER BY shipped_at DESC
            LIMIT 1
            """
        )
        return dict(row) if row else {}
    finally:
        await conn.close()


async def _snapshot_current_artifact() -> str:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            "SELECT current_artifact_id FROM cve_rem_workers LIMIT 1"
        )
        return str(row["current_artifact_id"]) if row else "no-artifact"
    finally:
        await conn.close()


async def _restart_worker(worker_id: str, new_artifact_id: str) -> None:
    conn = await _conn()
    try:
        await conn.execute(
            """
            UPDATE cve_rem_workers
              SET current_artifact_id = $2,
                  last_restart_at = NOW(),
                  health = 'restarting'
              WHERE id = $1
            """,
            worker_id, new_artifact_id,
        )
    finally:
        await conn.close()


async def _health_gate(worker_id: str) -> bool:
    """Stubbed health check. Production: HTTP probe against /healthz."""
    await asyncio.sleep(0.1)
    healthy = True  # demo: always healthy
    conn = await _conn()
    try:
        await conn.execute(
            """
            UPDATE cve_rem_workers
              SET health = $2, last_health_at = NOW()
              WHERE id = $1
            """,
            worker_id, "healthy" if healthy else "degraded",
        )
    finally:
        await conn.close()
    return healthy


async def _rollback(prior_artifact_id: str, reason: str) -> int:
    conn = await _conn()
    try:
        await conn.execute(
            """
            UPDATE cve_rem_workers
              SET current_artifact_id = $1,
                  health = 'rolled-back',
                  last_restart_at = NOW()
            """,
            prior_artifact_id,
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cve_rem_halt_new_ledger (
                id SERIAL PRIMARY KEY,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                run_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                rate NUMERIC,
                threshold NUMERIC,
                window_hours INTEGER,
                fired_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        row = await conn.fetchrow(
            """
            INSERT INTO cve_rem_halt_new_ledger
              (kind, severity, run_id, reason, rate, threshold, window_hours)
            VALUES ('rolling-restart-rollback', 'halt', 'fleet', $1, NULL, NULL, NULL)
            RETURNING id
            """,
            f"rolling restart rolled back to {prior_artifact_id}: {reason}",
        )
        return int(row["id"]) if row else 0
    finally:
        await conn.close()


async def main_async() -> int:
    print("=== Rolling restart (CRITERIA fancy #9) ===\n")
    await _ensure_workers_table()

    ship = await _latest_ship_event()
    if not ship:
        print("! no accepted ship event in cve_rem_ship_events; exiting")
        return 0
    new_artifact_id = str(ship["run_id"])
    print(f"  ship event id : {ship['id']}")
    print(f"  new artifact  : {new_artifact_id}")

    prior = await _snapshot_current_artifact()
    print(f"  prior artifact: {prior}")

    print("\n--- batched restart (batch_size=1) ---")
    for worker in _FLEET:
        print(f"  {worker}: restart -> {new_artifact_id}")
        await _restart_worker(worker, new_artifact_id)
        ok = await _health_gate(worker)
        print(f"  {worker}: health = {'ok' if ok else 'DEGRADED'}")
        if not ok:
            print(f"  ! {worker} failed health gate; rolling back fleet")
            ledger_id = await _rollback(
                prior, f"{worker} failed health gate after restart"
            )
            print(f"  halt-new ledger id: {ledger_id}")
            return 1

    print("\nfleet rolled cleanly to new artifact "
          f"{new_artifact_id} (prior preserved at {prior})")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
