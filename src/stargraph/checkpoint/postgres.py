# SPDX-License-Identifier: Apache-2.0
"""Postgres checkpointer driver -- asyncpg + pgbouncer-safe (FR-18, design §3.2.4).

The :class:`PostgresCheckpointer` is the multi-DC / shared-Postgres storage
driver for the engine. It opens an :class:`asyncpg.Pool` configured to
survive pgbouncer transaction-mode pooling (no prepared-statement cache,
asyncpg #1058), to stream JSONB through orjson via the text wire format
(asyncpg #623's binary jsonb codec is broken), to keep TCP connections
alive across NAT idle timeouts (research §4 amendment 5), and to close
gracefully under cancellation (asyncpg #290 -- mid-cancel partial close
leaves dangling sockets).

All Stargraph tables live under the ``stargraph`` schema (Nautilus coexistence;
design §3.2.4, AC-11.3). The pool's ``server_settings`` carry
``search_path=stargraph,public`` so unqualified statements resolve correctly
even from a shared-DB tenant.

This driver structurally implements :class:`stargraph.checkpoint.Checkpointer`
-- pin enforced by the ``TYPE_CHECKING`` Protocol assignment at the bottom.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

import asyncpg  # pyright: ignore[reportMissingTypeStubs]

from stargraph.checkpoint._codec import _init_jsonb_codec
from stargraph.checkpoint.protocol import Checkpoint, RunSummary

if TYPE_CHECKING:
    from stargraph.checkpoint.protocol import Checkpointer

__all__ = ["PostgresCheckpointer", "close_pool", "create_pool"]


# --------------------------------------------------------------------------- #
# Module-level pool factory + shutdown helper                                 #
# --------------------------------------------------------------------------- #


_SERVER_SETTINGS: dict[str, str] = {
    "tcp_keepalives_idle": "60",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "3",
    "application_name": "stargraph.engine",
    "search_path": "stargraph,public",
}


async def create_pool(dsn: str) -> Any:
    """Open a pgbouncer-safe :class:`asyncpg.Pool` (design §3.2.4).

    * ``statement_cache_size=0`` -- pgbouncer txn-mode rotates backends mid
      transaction, so server-side prepared statements cached client-side
      collide on the next checkout (asyncpg #1058).
    * ``server_settings`` -- TCP keepalives bound NAT/firewall idle eviction,
      ``application_name`` tags pg_stat_activity, ``search_path`` puts the
      ``stargraph`` schema first so unqualified DDL/DML resolves correctly.
    * ``init=_init_jsonb_codec`` -- registers the orjson text-format JSONB
      codec on every connection (binary format is broken, asyncpg #623).
    """
    return await asyncpg.create_pool(  # pyright: ignore[reportUnknownMemberType]
        dsn,
        min_size=1,
        max_size=10,
        max_inactive_connection_lifetime=300,
        statement_cache_size=0,
        server_settings=_SERVER_SETTINGS,
        init=_init_jsonb_codec,
    )


async def close_pool(pool: Any, *, timeout: float = 10.0) -> None:  # noqa: ASYNC109 -- design §3.2.4 mandates explicit wait_for(timeout=10) bound
    """Shutdown ``pool`` gracefully, falling back to ``terminate`` (design §3.2.4).

    asyncpg #290: a mid-cancel partial close leaves dangling sockets.
    Wrap the close in :func:`asyncio.shield` (so an outer cancel can't
    abort it part-way) and bound it with :func:`asyncio.wait_for` so a
    stuck connection doesn't hang shutdown forever; on timeout, terminate
    the pool ungracefully.
    """
    try:
        await asyncio.shield(asyncio.wait_for(pool.close(), timeout=timeout))
    except TimeoutError:
        pool.terminate()


# --------------------------------------------------------------------------- #
# Schema migration                                                            #
# --------------------------------------------------------------------------- #


_SCHEMA_DDL: tuple[str, ...] = (
    "CREATE SCHEMA IF NOT EXISTS stargraph",
    """
    CREATE TABLE IF NOT EXISTS stargraph.checkpoints (
        run_id            TEXT NOT NULL,
        step_idx          INTEGER NOT NULL,
        branch_id         TEXT NOT NULL DEFAULT '',
        parent_step_idx   INTEGER,
        ts                TIMESTAMPTZ NOT NULL,
        state_snapshot    JSONB NOT NULL,
        clips_facts       JSONB NOT NULL,
        last_node         TEXT NOT NULL,
        next_action       JSONB,
        structural_hash   TEXT NOT NULL,
        runtime_hash      TEXT NOT NULL,
        side_effects_hash TEXT NOT NULL,
        parent_run_id     TEXT,
        PRIMARY KEY (run_id, step_idx, branch_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_ts
        ON stargraph.checkpoints(run_id, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_hash
        ON stargraph.checkpoints(structural_hash, run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_parent
        ON stargraph.checkpoints(parent_run_id)
        WHERE parent_run_id IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS stargraph.runs (
        run_id        TEXT PRIMARY KEY,
        graph_hash    TEXT NOT NULL,
        runtime_hash  TEXT NOT NULL,
        started_at    TIMESTAMPTZ NOT NULL,
        last_step_at  TIMESTAMPTZ NOT NULL,
        status        TEXT NOT NULL
            CHECK (status IN ('running','done','failed','paused')),
        parent_run_id TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runs_started
        ON stargraph.runs(started_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS stargraph.stargraph_schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL
    )
    """,
)


# --------------------------------------------------------------------------- #
# Driver                                                                      #
# --------------------------------------------------------------------------- #


class PostgresCheckpointer:
    """asyncpg + pgbouncer-safe implementation of :class:`Checkpointer`.

    Construction is cheap and synchronous; the pool is opened by the first
    :py:meth:`bootstrap` call. Subsequent ``bootstrap`` calls on the same
    instance are a no-op.

    The pool's ``server_settings`` are surfaced via the
    :py:attr:`server_settings` property so callers (and the
    test_server_settings_tcp_keepalives integration test) can verify the
    configured runtime values without round-tripping the DB.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn: str = dsn
        self._pool: Any | None = None
        self._schema_applied: bool = False

    # ----- Lifecycle ------------------------------------------------------ #

    async def _ensure_pool(self) -> Any:
        """Return a pool bound to the running event loop, recreating if stale.

        Pools are loop-bound: an :class:`asyncpg.Pool` created under one event
        loop cannot be used from another. The Checkpointer surface is called
        from arbitrary entry points (incl. tests that wrap each step in its
        own :func:`asyncio.run`), so we treat the stored pool as a cache and
        rebuild it whenever the running loop differs from the pool's loop.
        """
        running = asyncio.get_running_loop()
        pool = self._pool
        if pool is not None and getattr(pool, "_loop", None) is running:
            return pool
        # Stale or absent: drop it (closed loops can't run pool.close()) and rebuild.
        self._pool = None
        new_pool = await create_pool(self._dsn)
        self._pool = new_pool
        return new_pool

    async def bootstrap(self) -> None:
        """Idempotent schema/migration bootstrap (design §3.2.5)."""
        pool = await self._ensure_pool()
        if self._schema_applied:
            return
        async with pool.acquire() as conn:
            for stmt in _SCHEMA_DDL:
                await conn.execute(stmt)
            await conn.execute(
                """
                INSERT INTO stargraph.stargraph_schema_version (version, applied_at)
                VALUES ($1, $2)
                ON CONFLICT (version) DO NOTHING
                """,
                1,
                datetime.now(tz=_UTC()),
            )
        self._schema_applied = True

    async def close_pool(self) -> None:
        """Shielded + bounded pool shutdown (asyncpg #290).

        If the cached pool is bound to a different (or already-closed) event
        loop -- e.g. the caller wraps each step in its own
        :func:`asyncio.run` -- ``await pool.close()`` would fault with
        ``Event loop is closed``. Fall back to the synchronous
        :meth:`Pool.terminate` in that case; the loop owning the sockets is
        gone, so there's nothing graceful to do.
        """
        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        pool_loop = getattr(pool, "_loop", None)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if pool_loop is not running or (pool_loop is not None and pool_loop.is_closed()):
            # Loop owning the sockets is gone; pool.terminate() schedules
            # connection_lost callbacks on that loop and faults with
            # ``Event loop is closed``. Swallow it -- the loop's destruction
            # already released the FDs.
            with contextlib.suppress(RuntimeError):
                pool.terminate()
            return
        await close_pool(pool)

    # ----- Accessors ------------------------------------------------------ #

    @property
    def server_settings(self) -> dict[str, str]:
        """Pool ``server_settings`` (TCP keepalives, application_name, search_path)."""
        return dict(_SERVER_SETTINGS)

    # ----- Checkpointer Protocol ----------------------------------------- #

    async def write(self, checkpoint: Checkpoint) -> None:
        """Persist a :class:`Checkpoint` row (design §3.2.2)."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO stargraph.checkpoints (
                    run_id, step_idx, branch_id, parent_step_idx, ts,
                    state_snapshot, clips_facts, last_node, next_action,
                    structural_hash, runtime_hash, side_effects_hash,
                    parent_run_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (run_id, step_idx, branch_id) DO UPDATE SET
                    parent_step_idx   = EXCLUDED.parent_step_idx,
                    ts                = EXCLUDED.ts,
                    state_snapshot    = EXCLUDED.state_snapshot,
                    clips_facts       = EXCLUDED.clips_facts,
                    last_node         = EXCLUDED.last_node,
                    next_action       = EXCLUDED.next_action,
                    structural_hash   = EXCLUDED.structural_hash,
                    runtime_hash      = EXCLUDED.runtime_hash,
                    side_effects_hash = EXCLUDED.side_effects_hash,
                    parent_run_id     = EXCLUDED.parent_run_id
                """,
                checkpoint.run_id,
                checkpoint.step,
                checkpoint.branch_id if checkpoint.branch_id is not None else "",
                checkpoint.parent_step_idx,
                checkpoint.timestamp,
                checkpoint.state,
                checkpoint.clips_facts,
                checkpoint.last_node,
                checkpoint.next_action,
                checkpoint.graph_hash,
                checkpoint.runtime_hash,
                checkpoint.side_effects_hash,
                checkpoint.parent_run_id,
            )

    async def read_latest(self, run_id: str) -> Checkpoint | None:
        """Return the highest-step checkpoint for ``run_id`` or ``None``."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT run_id, step_idx, branch_id, parent_step_idx, ts,
                       state_snapshot, clips_facts, last_node, next_action,
                       structural_hash, runtime_hash, side_effects_hash,
                       parent_run_id
                FROM stargraph.checkpoints
                WHERE run_id = $1
                ORDER BY step_idx DESC
                LIMIT 1
                """,
                run_id,
            )
        return _row_to_checkpoint(row) if row is not None else None

    async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None:
        """Return the checkpoint at ``(run_id, step)`` or ``None``."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT run_id, step_idx, branch_id, parent_step_idx, ts,
                       state_snapshot, clips_facts, last_node, next_action,
                       structural_hash, runtime_hash, side_effects_hash,
                       parent_run_id
                FROM stargraph.checkpoints
                WHERE run_id = $1 AND step_idx = $2
                LIMIT 1
                """,
                run_id,
                step,
            )
        return _row_to_checkpoint(row) if row is not None else None

    async def list_runs(
        self, *, since: datetime | None = None, limit: int = 100
    ) -> list[RunSummary]:
        """Return run summaries, optionally filtered by ``started_at >= since``."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            if since is None:
                rows = await conn.fetch(
                    """
                    SELECT run_id, graph_hash, started_at, last_step_at, status,
                           parent_run_id
                    FROM stargraph.runs
                    ORDER BY started_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT run_id, graph_hash, started_at, last_step_at, status,
                           parent_run_id
                    FROM stargraph.runs
                    WHERE started_at >= $1
                    ORDER BY started_at DESC
                    LIMIT $2
                    """,
                    since,
                    limit,
                )
        return [_row_to_run_summary(r) for r in rows]


# --------------------------------------------------------------------------- #
# Row decoders                                                                #
# --------------------------------------------------------------------------- #


def _row_to_checkpoint(row: Any) -> Checkpoint:
    """Hydrate a ``stargraph.checkpoints`` row into a :class:`Checkpoint`."""
    branch_id = cast("str", row["branch_id"])
    return Checkpoint(
        run_id=cast("str", row["run_id"]),
        step=int(cast("int", row["step_idx"])),
        branch_id=branch_id if branch_id else None,
        parent_step_idx=cast("int | None", row["parent_step_idx"]),
        graph_hash=cast("str", row["structural_hash"]),
        runtime_hash=cast("str", row["runtime_hash"]),
        state=cast("dict[str, Any]", row["state_snapshot"]),
        clips_facts=cast("list[Any]", row["clips_facts"]),
        last_node=cast("str", row["last_node"]),
        next_action=cast("dict[str, Any] | None", row["next_action"]),
        timestamp=cast("datetime", row["ts"]),
        parent_run_id=cast("str | None", row["parent_run_id"]),
        side_effects_hash=cast("str", row["side_effects_hash"]),
    )


def _row_to_run_summary(row: Any) -> RunSummary:
    """Hydrate a ``stargraph.runs`` row into a :class:`RunSummary`."""
    return RunSummary(
        run_id=cast("str", row["run_id"]),
        graph_hash=cast("str", row["graph_hash"]),
        started_at=cast("datetime", row["started_at"]),
        last_step_at=cast("datetime", row["last_step_at"]),
        status=row["status"],
        parent_run_id=cast("str | None", row["parent_run_id"]),
    )


def _UTC() -> Any:  # noqa: N802 -- helper alias keeps datetime import minimal
    """Local alias for :data:`datetime.UTC` (avoids re-import noise above)."""
    from datetime import UTC

    return UTC


# --------------------------------------------------------------------------- #
# Static Protocol conformance                                                 #
# --------------------------------------------------------------------------- #
# Pin PostgresCheckpointer to the Checkpointer Protocol at type-check time.
if TYPE_CHECKING:
    _: Checkpointer = PostgresCheckpointer.__new__(PostgresCheckpointer)
