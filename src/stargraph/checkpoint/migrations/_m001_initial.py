# SPDX-License-Identifier: Apache-2.0
"""Migration 1 -- initial schema (design §3.2.2).

Creates three tables:

* ``checkpoints``           -- per-step snapshots written by the engine
* ``runs``                  -- run-level summary rows for inspect/CLI
* ``stargraph_schema_version`` -- monotonically-applied migration log

Plus three indexes on ``checkpoints`` (timestamp, structural-hash lookup,
counterfactual parent_run_id where non-null) and one on ``runs.started_at``.

All ``CREATE`` statements use ``IF NOT EXISTS`` so the migration is
idempotent on its own; the wider runner additionally consults
:py:meth:`Driver.read_schema_version` and skips already-applied migrations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

__all__ = ["up"]


# Schema literal kept as one DDL string per statement so failures point at a
# specific table -- aiosqlite ``executescript`` would obscure that.
_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        run_id            TEXT NOT NULL,
        step_idx          INTEGER NOT NULL,
        branch_id         TEXT,
        parent_step_idx   INTEGER,
        ts                TEXT NOT NULL,
        state_snapshot    BLOB NOT NULL,
        clips_facts       BLOB NOT NULL,
        last_node         TEXT NOT NULL,
        next_action       BLOB,
        structural_hash   TEXT NOT NULL,
        runtime_hash      TEXT NOT NULL,
        side_effects_hash TEXT NOT NULL,
        parent_run_id     TEXT,
        PRIMARY KEY (run_id, step_idx, branch_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_ts
        ON checkpoints(run_id, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_hash
        ON checkpoints(structural_hash, run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_parent
        ON checkpoints(parent_run_id)
        WHERE parent_run_id IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id        TEXT PRIMARY KEY,
        graph_hash    TEXT NOT NULL,
        runtime_hash  TEXT NOT NULL,
        started_at    TEXT NOT NULL,
        last_step_at  TEXT NOT NULL,
        status        TEXT NOT NULL
            CHECK (status IN ('running','done','failed','paused')),
        parent_run_id TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runs_started
        ON runs(started_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS stargraph_schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
)


async def up(db: aiosqlite.Connection) -> None:
    """Apply migration 1 to ``db``. Idempotent (every statement is ``IF NOT EXISTS``)."""
    for stmt in _DDL:
        await db.execute(stmt)
