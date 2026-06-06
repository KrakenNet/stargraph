# SPDX-License-Identifier: Apache-2.0
"""Migration 2 -- run-history + pending-runs tables (design §6.1, §6.5).

Adds two serve-layer tables that share the Checkpointer's SQLite
connection (design §6.5: "Lives in the same Checkpointer DB").

* ``runs_history`` -- per-run summary row owned by
  :class:`stargraph.serve.history.RunHistory`. Schema mirrors design §6.5:
  ``(run_id PK, status, duration_ms, graph_hash, trigger_source,
  started_at, finished_at, parent_run_id, created_at)``. Indexed on
  ``(status, started_at)`` for ``GET /runs?status=&since=&limit=``
  filter+pagination.
* ``pending_runs`` -- durable scheduler queue rows satisfying the
  :class:`stargraph.serve.scheduler.PendingStore` Protocol (FR-9, FR-10).
  Survives restart so in-flight triggers replay on startup.

Distinct from migration 1's ``runs`` table (Checkpointer's run-summary
index — different schema, indexed on ``started_at`` alone). The
serve-layer table is named ``runs_history`` to keep the two
unambiguous in SQL log lines and `EXPLAIN QUERY PLAN` output.

All ``CREATE`` statements are ``IF NOT EXISTS`` so the migration is
idempotent on its own; the wider runner additionally consults
``stargraph_schema_version`` and skips already-applied migrations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

__all__ = ["up"]


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS runs_history (
        run_id          TEXT PRIMARY KEY,
        status          TEXT NOT NULL,
        duration_ms     INTEGER,
        graph_hash      TEXT NOT NULL,
        trigger_source  TEXT NOT NULL
            CHECK (trigger_source IN ('manual','cron','webhook')),
        started_at      TEXT NOT NULL,
        finished_at     TEXT,
        parent_run_id   TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runs_history_status_started
        ON runs_history(status, started_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_runs (
        run_id           TEXT PRIMARY KEY,
        graph_id         TEXT NOT NULL,
        params_json      TEXT NOT NULL,
        idempotency_key  TEXT NOT NULL UNIQUE,
        scheduled_fire   TEXT NOT NULL,
        created_at       TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pending_runs_idem
        ON pending_runs(idempotency_key)
    """,
)


async def up(db: aiosqlite.Connection) -> None:
    """Apply migration 2 to ``db``. Idempotent."""
    for stmt in _DDL:
        await db.execute(stmt)
