# SPDX-License-Identifier: Apache-2.0
""":class:`RunHistory` -- run-history + pending-runs persistence (design §6.1, §6.5).

Owns two SQLite tables that live in the Checkpointer DB (per design §6.5
"Lives in the same Checkpointer DB; reuses the Checkpointer's connection
pool"):

* ``runs_history`` -- per-run summary row written on enqueue and updated
  on terminal transitions. Schema mirrors design §6.5: ``(run_id PK,
  status, duration_ms, graph_hash, trigger_source, started_at,
  finished_at, parent_run_id, created_at)``. Indexed on
  ``(status, started_at)`` for ``GET /runs?status=&since=&limit=``
  filter+pagination (FR-22, FR-23, AC-10.1).
* ``pending_runs`` -- durable scheduler queue rows. Satisfies the
  :class:`harbor.serve.scheduler.PendingStore` Protocol so the
  :class:`harbor.serve.scheduler.Scheduler` can be wired with a single
  ``run_history`` instance and get both run-history persistence and
  durable pending-queue replay-on-restart for free (FR-9, FR-10).

Wiring (the optional pattern):

* :class:`harbor.serve.scheduler.Scheduler` accepts an optional
  ``run_history: RunHistory | None`` constructor arg. When wired, the
  scheduler calls :meth:`insert_pending` on enqueue and
  :meth:`update_status` on terminal transitions.
* :func:`harbor.serve.lifecycle.cancel_run` /
  :func:`harbor.serve.lifecycle.pause_run` accept an optional
  ``run_history`` and call :meth:`update_status` after the GraphRun
  cancel/pause boundary so the runs_history table tracks the
  cancelled/paused state for ``GET /runs?status=...`` queries.

The :class:`RunHistory` does *not* own its connection lifecycle: it
takes an :class:`aiosqlite.Connection` (typically from
:class:`harbor.checkpoint.sqlite.SQLiteCheckpointer`'s internal
``_db``) so single-writer constraints are respected. Migration is
already applied by the Checkpointer's ``bootstrap()`` (registered in
:mod:`harbor.checkpoint.migrations`); calling :meth:`bootstrap` here
is a defensive idempotent no-op that re-runs the ``CREATE TABLE IF NOT
EXISTS`` DDL so :class:`RunHistory` is usable in test scenarios that
construct it without a Checkpointer.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field

from harbor.audit.jsonl import unwrap_audit_record
from harbor.serve.scheduler import PendingRun

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import aiosqlite

_logger = logging.getLogger(__name__)


__all__ = [
    "RunHistory",
    "RunRecord",
    "TriggerSource",
]


TriggerSource = Literal["manual", "cron", "webhook"]


class RunRecord(BaseModel):
    """One ``runs_history`` row (design §6.5).

    Mirrors the SQLite table schema 1-to-1. Timestamps are stored as
    ISO-8601 TEXT in SQLite (zero-cost lexicographic ordering matches
    chronological ordering for fixed-tz strings); we hydrate them to
    :class:`datetime` here so callers don't reparse.
    """

    run_id: str
    status: str
    graph_hash: str
    trigger_source: TriggerSource
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    parent_run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _utcnow_iso() -> str:
    """ISO-8601 UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat()


class RunHistory:
    """Run-history + pending-runs persistence over an aiosqlite connection.

    Construction is cheap; pass an already-open
    :class:`aiosqlite.Connection`. Call :meth:`bootstrap` once before
    first use to ensure the DDL has been applied (idempotent — safe to
    call when the connection already came from a bootstrapped
    Checkpointer).

    Implements the :class:`harbor.serve.scheduler.PendingStore` Protocol
    via :meth:`put_pending` / :meth:`delete_pending` /
    :meth:`list_pending` / :meth:`has_pending_for_key` so a single
    instance can be passed to :class:`Scheduler` for both run-history
    and durable-queue concerns.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        jsonl_audit_path: Path | None = None,
    ) -> None:
        self._db: aiosqlite.Connection = db
        # JSONL audit-file offsets index (design §6.5 "JSONL pairing"):
        # maps ``(run_id, step) -> byte_offset`` so the inspect-CLI
        # timeline view can ``os.lseek`` straight to a specific event
        # without a full file scan. Built on :meth:`bootstrap` (or lazy
        # on first :meth:`get_event_offset` call) by walking the JSONL
        # file once. POC scope: fail-soft when ``jsonl_audit_path`` is
        # ``None`` -- :meth:`get_event_offset` returns ``None`` for all
        # lookups. Phase 3 wires the lifespan factory's audit-sink path
        # through here so the index is always populated.
        self._jsonl_audit_path: Path | None = jsonl_audit_path
        self._event_offsets: dict[tuple[str, int], int] = {}
        self._event_offsets_built: bool = False

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    async def bootstrap(self) -> None:
        """Apply the ``runs_history`` + ``pending_runs`` DDL (idempotent).

        The Checkpointer's migration runner already applies this via
        :mod:`harbor.checkpoint.migrations._m002_run_history` on its
        own ``bootstrap()``; this method exists so :class:`RunHistory`
        is usable from tests that construct an aiosqlite connection
        directly without the Checkpointer's lock + WAL machinery.
        """
        # Mirror migrations._m002_run_history._DDL; duplication is
        # intentional so tests can use this without depending on the
        # migration runner.
        await self._db.execute(
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
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_history_status_started "
            "ON runs_history(status, started_at)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_runs (
                run_id           TEXT PRIMARY KEY,
                graph_id         TEXT NOT NULL,
                params_json      TEXT NOT NULL,
                idempotency_key  TEXT NOT NULL UNIQUE,
                scheduled_fire   TEXT NOT NULL,
                created_at       TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_runs_idem ON pending_runs(idempotency_key)"
        )
        await self._db.commit()
        # Build the JSONL ``run_event_offsets`` index now if the audit
        # path was supplied -- one O(file-size) walk on bootstrap per
        # design §6.5 "built on startup".
        self._build_event_offsets()

    # ------------------------------------------------------------------ #
    # JSONL ``run_event_offsets`` index (design §6.5)                    #
    # ------------------------------------------------------------------ #

    def _build_event_offsets(self) -> None:
        """Walk the JSONL audit file and record ``(run_id, step) -> byte_offset``.

        Called from :meth:`bootstrap`; idempotent (subsequent calls
        rebuild the dict from scratch). Fail-soft when no path was
        supplied or the file does not exist yet -- the index stays
        empty and :meth:`get_event_offset` returns ``None`` for all
        lookups.

        The walk is synchronous + blocking but only runs once on
        bootstrap; for the POC retention window (default 30 days) the
        file is bounded at design §3.12's 100 MiB rotation ceiling, so
        a full scan is a fraction of a second on local disk. Phase 3
        may switch to incremental tail-following as new events land.

        Each line is the on-disk event record produced by
        :class:`harbor.audit.jsonl.JSONLAuditSink`. Two on-disk shapes
        per :meth:`JSONLAuditSink._encode`:

        * unsigned: bare event dict -- ``{"run_id": ..., "step": ...,
          ...}``
        * signed: ``{"event": {"run_id": ..., "step": ..., ...},
          "sig": "<hex>"}``

        We probe both shapes, extract ``run_id`` + ``step`` when both
        are present, and record the byte offset of the line's first
        byte. Missing/malformed lines are skipped silently (the index
        is best-effort; the canonical event-by-step lookup falls back
        to the Checkpointer in Phase 3).
        """
        self._event_offsets = {}
        self._event_offsets_built = True
        path = self._jsonl_audit_path
        if path is None or not path.exists():
            return
        try:
            with path.open("rb") as fh:
                offset = 0
                for line in fh:
                    line_offset = offset
                    offset += len(line)
                    if not line.strip():
                        continue
                    try:
                        record: Any = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    # Unwrap signed envelope if present.
                    record_dict = cast("dict[str, Any]", record)
                    payload_raw = unwrap_audit_record(record_dict)
                    if not isinstance(payload_raw, dict):
                        continue
                    payload = cast("dict[str, Any]", payload_raw)
                    run_id = payload.get("run_id")
                    step = payload.get("step")
                    if isinstance(run_id, str) and isinstance(step, int):
                        self._event_offsets[(run_id, step)] = line_offset
        except OSError as exc:
            # Best-effort: a transient read error leaves the index
            # empty; lookups return ``None``. Log once at debug so the
            # lifespan factory's wiring problems surface in tests
            # without spamming production logs.
            _logger.debug("run_event_offsets: failed to walk %s: %s", path, exc)

    def get_event_offset(self, run_id: str, step: int) -> int | None:
        """Return the JSONL byte offset for ``(run_id, step)`` or ``None``.

        Lazy-builds the index on first call when :meth:`bootstrap` was
        not invoked (e.g. in a test that constructs :class:`RunHistory`
        directly with a `jsonl_audit_path` and never calls bootstrap).
        Fail-soft when no audit path was supplied at construction --
        always returns ``None``.
        """
        if not self._event_offsets_built:
            self._build_event_offsets()
        return self._event_offsets.get((run_id, step))

    @property
    def jsonl_audit_path(self) -> Path | None:
        """The configured JSONL audit-file path (or ``None`` if absent)."""
        return self._jsonl_audit_path

    # ------------------------------------------------------------------ #
    # runs_history API                                                   #
    # ------------------------------------------------------------------ #

    async def insert_pending(
        self,
        run_id: str,
        graph_hash: str,
        trigger_source: TriggerSource,
        *,
        parent_run_id: str | None = None,
    ) -> None:
        """Insert a fresh ``pending`` row into ``runs_history``.

        Called by the scheduler on enqueue. ``status='pending'``,
        ``started_at=created_at=now``, ``finished_at=duration_ms=NULL``.
        Subsequent :meth:`update_status` calls fill in the terminal
        fields. ``INSERT OR IGNORE`` so a replay-on-restart that
        re-enqueues an already-recorded run does not duplicate the
        row (the row's ``created_at`` is preserved as the original
        enqueue time).
        """
        now = _utcnow_iso()
        await self._db.execute(
            """
            INSERT OR IGNORE INTO runs_history (
                run_id, status, duration_ms, graph_hash, trigger_source,
                started_at, finished_at, parent_run_id, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, NULL, ?, ?)
            """,
            (
                run_id,
                "pending",
                graph_hash,
                trigger_source,
                now,
                parent_run_id,
                now,
            ),
        )
        await self._db.commit()

    async def update_status(
        self,
        run_id: str,
        status: str,
        *,
        finished_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Update ``status`` (and optionally terminal fields) for ``run_id``.

        For non-terminal transitions (``running``, ``paused``,
        ``awaiting-input``) ``finished_at``/``duration_ms`` should be
        ``None``. For terminal transitions (``done``, ``failed``,
        ``cancelled``, ``error``) the caller passes the wall-clock
        finish time and the elapsed milliseconds. If the row does not
        exist (e.g. lifecycle update arrives before the scheduler had
        a chance to insert), the UPDATE is a no-op — the scheduler's
        :meth:`insert_pending` always lands first in the documented
        flow, so this no-op is the rare race-window case.
        """
        # Update only the columns the caller supplied; preserve nulls
        # when finished_at/duration_ms are still TBD.
        await self._db.execute(
            """
            UPDATE runs_history
               SET status = ?,
                   finished_at = COALESCE(?, finished_at),
                   duration_ms = COALESCE(?, duration_ms)
             WHERE run_id = ?
            """,
            (
                status,
                finished_at.isoformat() if finished_at is not None else None,
                duration_ms,
                run_id,
            ),
        )
        await self._db.commit()

    async def get(self, run_id: str) -> RunRecord | None:
        """Return the ``RunRecord`` for ``run_id`` or ``None`` if absent."""
        async with self._db.execute(
            "SELECT run_id, status, duration_ms, graph_hash, trigger_source, "
            "started_at, finished_at, parent_run_id, created_at "
            "FROM runs_history WHERE run_id = ?",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_record(row) if row is not None else None

    async def list(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        trigger_source: TriggerSource | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Return ``runs_history`` rows matching the filter (design §6.5).

        Filters compose with ``AND``; ``since``/``until`` bound
        ``started_at`` (inclusive lower / exclusive upper, ISO-8601
        string compare which matches chronological order for fixed
        tz). Default ``limit=100`` matches design §6.5
        ("max ``limit=100``"). ``offset`` is provided for the simple
        admin pagination path; the canonical
        ``GET /runs?cursor=`` API uses an opaque cursor instead (see
        §6.5 — implemented at the route layer).
        """
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if since is not None:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            conditions.append("started_at < ?")
            params.append(until.isoformat())
        if trigger_source is not None:
            conditions.append("trigger_source = ?")
            params.append(trigger_source)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (
            "SELECT run_id, status, duration_ms, graph_hash, trigger_source, "
            "started_at, finished_at, parent_run_id, created_at "
            f"FROM runs_history {where} "
            "ORDER BY started_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        async with self._db.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def count(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        trigger_source: TriggerSource | None = None,
    ) -> int:
        """Return the total count of ``runs_history`` rows matching the filter.

        Mirrors :meth:`list`'s filter clauses so a paginated route can
        return a stable ``total`` alongside the page slice. POC scale:
        fine for retention-bounded history (default 30 days); Phase 3
        may switch to an estimate-or-omit pattern at very large scale.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if since is not None:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            conditions.append("started_at < ?")
            params.append(until.isoformat())
        if trigger_source is not None:
            conditions.append("trigger_source = ?")
            params.append(trigger_source)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM runs_history {where}"
        async with self._db.execute(sql, tuple(params)) as cur:
            row = await cur.fetchone()
        if row is None:
            return 0
        return int(row[0])

    # ------------------------------------------------------------------ #
    # PendingStore Protocol (design §6.1)                                #
    # ------------------------------------------------------------------ #

    async def put_pending(self, run: PendingRun) -> None:
        """Persist ``run`` so it survives a process restart.

        ``INSERT OR IGNORE`` — the scheduler treats duplicate
        idempotency keys as the dedupe contract (FR-9), so racing
        enqueues for the same key are a no-op rather than an error.
        """
        await self._db.execute(
            """
            INSERT OR IGNORE INTO pending_runs (
                run_id, graph_id, params_json, idempotency_key,
                scheduled_fire, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.graph_id,
                json.dumps(dict(run.params)),
                run.idempotency_key,
                run.scheduled_fire.isoformat(),
                _utcnow_iso(),
            ),
        )
        await self._db.commit()

    async def delete_pending(self, run_id: str) -> None:
        """Remove the pending row for ``run_id`` (terminal state reached)."""
        await self._db.execute(
            "DELETE FROM pending_runs WHERE run_id = ?",
            (run_id,),
        )
        await self._db.commit()

    async def list_pending(self) -> list[PendingRun]:
        """Return all pending rows (used at startup for replay)."""
        async with self._db.execute(
            "SELECT run_id, graph_id, params_json, idempotency_key, "
            "scheduled_fire FROM pending_runs ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_pending(r) for r in rows]

    async def has_pending_for_key(self, idempotency_key: str) -> bool:
        """Return ``True`` if a pending row already exists for ``idempotency_key``."""
        async with self._db.execute(
            "SELECT 1 FROM pending_runs WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        ) as cur:
            row = await cur.fetchone()
        return row is not None


# --------------------------------------------------------------------------- #
# Row decoders                                                                #
# --------------------------------------------------------------------------- #


def _row_to_record(row: Any) -> RunRecord:
    (
        run_id,
        status,
        duration_ms,
        graph_hash,
        trigger_source,
        started_at,
        finished_at,
        parent_run_id,
        created_at,
    ) = tuple(row)
    return RunRecord(
        run_id=run_id,
        status=status,
        duration_ms=duration_ms,
        graph_hash=graph_hash,
        trigger_source=trigger_source,
        started_at=datetime.fromisoformat(started_at),
        finished_at=(datetime.fromisoformat(finished_at) if finished_at else None),
        parent_run_id=parent_run_id,
        created_at=datetime.fromisoformat(created_at),
    )


def _row_to_pending(row: Any) -> PendingRun:
    (run_id, graph_id, params_json, idempotency_key, scheduled_fire) = tuple(row)
    params: Mapping[str, Any] = json.loads(params_json)
    return PendingRun(
        run_id=run_id,
        graph_id=graph_id,
        params=params,
        idempotency_key=idempotency_key,
        scheduled_fire=datetime.fromisoformat(scheduled_fire),
    )
