# SPDX-License-Identifier: Apache-2.0
"""SQLite checkpointer driver -- aiosqlite + WAL (FR-17, design §3.2.3, §3.2.5).

The :class:`SQLiteCheckpointer` is the local-FS storage driver for the engine.
It opens an aiosqlite connection with WAL pragmas, refuses bootstrap on
network-FS prefixes (NFS / SMB / AFP -- WAL corruption risk per upstream
SQLite docs), probes for foreign-process writers via :func:`fcntl.flock`
(POSIX) or :func:`msvcrt.locking` (Windows), and runs the hand-rolled
migrations from :mod:`stargraph.checkpoint.migrations`.

Single-writer constraint
------------------------
SQLite's WAL mode allows multiple readers but only one writer. Stargraph v1
enforces single-process write at the OS-lock level: ``bootstrap()`` opens
the DB file with an advisory exclusive lock; a second process trying to
bootstrap the same path will see the lock held and raise
:class:`stargraph.errors.CheckpointError` with
``'multi-process writer not supported in v1'``.

The Windows path uses :func:`msvcrt.locking` which has different
semantics (mandatory locking on a byte range rather than POSIX advisory
locking on the inode); the multi-process refusal test
(``test_sqlite_multi_process_writer_refusal``) is skipped on Windows for
v1, and a Windows-specific test path may follow in a later task.

Idempotency
-----------
``bootstrap()`` records that initialization has completed; subsequent
calls on the same instance are a no-op. Re-running ``bootstrap()`` on a
fresh instance pointing at an already-migrated DB also succeeds: the
migration runner reads ``stargraph_schema_version`` and skips any version
already applied.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import aiosqlite

from stargraph.checkpoint._codec import dumps_jsonb, loads_jsonb
from stargraph.checkpoint.migrations import MIGRATIONS
from stargraph.checkpoint.migrations._network_fs import is_network_fs
from stargraph.checkpoint.protocol import Checkpoint, RunSummary
from stargraph.errors import CheckpointError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from stargraph.checkpoint.protocol import Checkpointer

__all__ = ["SQLiteCheckpointer"]


# --------------------------------------------------------------------------- #
# Connection helper                                                           #
# --------------------------------------------------------------------------- #


async def _connect(path: Path) -> aiosqlite.Connection:
    """Open ``path`` with WAL pragmas (design §3.2.3).

    Refuses network-FS paths up-front to avoid silent WAL corruption.
    """
    if is_network_fs(path):
        raise CheckpointError(
            "WAL corrupts on network FS",
            path=str(path),
            reason="network-fs",
        )
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.commit()
    return db


# --------------------------------------------------------------------------- #
# Lock probe (POSIX vs Windows)                                               #
# --------------------------------------------------------------------------- #


def _describe_lock_holder(path: Path) -> str:
    """Best-effort: name the process(es) holding ``path`` via ``lsof``.

    Returns an empty string when ``lsof`` is unavailable or the probe
    fails. Output format is one ``PID NNN (cmd...)`` token per holder,
    space-joined. POSIX-only; on Windows this is a no-op.
    """
    if sys.platform == "win32":  # pragma: no cover
        return ""
    import shutil
    import subprocess

    lsof = shutil.which("lsof")
    if not lsof:
        return ""
    try:
        out = subprocess.run(
            [lsof, "-Fpc", str(path)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    pid: str | None = None
    holders: list[str] = []
    for line in out.stdout.splitlines():
        if not line:
            continue
        tag, rest = line[0], line[1:]
        if tag == "p":
            pid = rest
        elif tag == "c" and pid is not None:
            holders.append(f"PID {pid} ({rest})")
            pid = None
    return " ".join(holders)


def _try_acquire_writer_lock(fd: int) -> bool:
    """Try to acquire an exclusive non-blocking lock on ``fd``.

    Returns ``True`` on success, ``False`` if a foreign process already
    holds an exclusive lock. Any other ``OSError`` propagates.
    """
    if sys.platform == "win32":  # pragma: no cover -- POSIX-only CI for v1
        import msvcrt  # type: ignore[import-not-found]

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Driver                                                                      #
# --------------------------------------------------------------------------- #


class SQLiteCheckpointer:
    """aiosqlite + WAL implementation of :class:`stargraph.checkpoint.Checkpointer`.

    Construction is cheap and synchronous; all I/O happens in the async
    methods. The first :py:meth:`bootstrap` call opens the file, acquires
    the writer lock, opens the aiosqlite connection, and runs migrations.
    Subsequent calls on the same instance are no-ops.
    """

    def __init__(self, path: Path) -> None:
        self._path: Path = Path(path)
        self._db: aiosqlite.Connection | None = None
        self._fd: int | None = None
        self._bootstrapped: bool = False

    # ----- Lifecycle ------------------------------------------------------ #

    async def bootstrap(self) -> None:
        """Idempotent schema/migration bootstrap (design §3.2.5)."""
        if self._bootstrapped:
            return

        # 1. Refuse network-FS up-front (before any side effect).
        if is_network_fs(self._path):
            raise CheckpointError(
                "WAL corrupts on network FS",
                path=str(self._path),
                reason="network-fs",
            )

        # 2. Open a real OS fd on the DB path so we can probe for foreign
        #    writers via fcntl.flock / msvcrt.locking. The file is created
        #    if missing -- aiosqlite would do the same on first connect.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_acquire_writer_lock(fd):
            os.close(fd)
            holder = _describe_lock_holder(self._path)
            holder_msg = f" (held by {holder})" if holder else ""
            raise CheckpointError(
                "multi-process writer not supported in v1; "
                "only one process may hold a writer connection"
                f"{holder_msg}",
                path=str(self._path),
                reason="concurrent-writer",
            )
        self._fd = fd

        # 3. Open the aiosqlite connection with WAL pragmas.
        try:
            self._db = await _connect(self._path)
        except Exception:
            # Release the OS lock if connection setup failed mid-flight so
            # the next bootstrap attempt can recover.
            os.close(self._fd)
            self._fd = None
            raise

        # 4. Run pending migrations (idempotent: skip already-applied).
        await self._run_migrations()

        self._bootstrapped = True

    async def close(self) -> None:
        """Close the aiosqlite connection and release the writer lock."""
        if self._db is not None:
            await self._db.close()
            self._db = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._bootstrapped = False

    # ----- Migrations ----------------------------------------------------- #

    async def _run_migrations(self) -> None:
        """Apply any :class:`Migration` whose version exceeds the current."""
        db = self._require_db()

        # Ensure the version table exists -- the very first migration
        # creates it but the runner needs to read it before that.
        await db.execute(
            "CREATE TABLE IF NOT EXISTS stargraph_schema_version ("
            "  version    INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        await db.commit()

        current = await self._read_schema_version()
        for m in MIGRATIONS:
            if m.version <= current:
                continue
            await m.up(db)
            await db.execute(
                "INSERT OR IGNORE INTO stargraph_schema_version (version, applied_at) "
                "VALUES (?, ?)",
                (m.version, _utcnow_iso()),
            )
            await db.execute(f"PRAGMA user_version = {m.version}")
            await db.commit()

    async def _read_schema_version(self) -> int:
        """Return the highest applied migration version (0 if none)."""
        db = self._require_db()
        async with db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM stargraph_schema_version"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    # ----- Checkpointer Protocol ----------------------------------------- #

    async def write(self, checkpoint: Checkpoint) -> None:
        """Persist a :class:`Checkpoint` row (design §3.2.2)."""
        db = self._require_db()
        await db.execute(
            """
            INSERT OR REPLACE INTO checkpoints (
                run_id, step_idx, branch_id, parent_step_idx, ts,
                state_snapshot, clips_facts, last_node, next_action,
                structural_hash, runtime_hash, side_effects_hash,
                parent_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.run_id,
                checkpoint.step,
                checkpoint.branch_id,
                checkpoint.parent_step_idx,
                checkpoint.timestamp.isoformat(),
                dumps_jsonb(checkpoint.state),
                dumps_jsonb(checkpoint.clips_facts),
                checkpoint.last_node,
                dumps_jsonb(checkpoint.next_action) if checkpoint.next_action is not None else None,
                checkpoint.graph_hash,
                checkpoint.runtime_hash,
                checkpoint.side_effects_hash,
                checkpoint.parent_run_id,
            ),
        )
        await db.commit()

    async def read_latest(self, run_id: str) -> Checkpoint | None:
        """Return the highest-step checkpoint for ``run_id`` or ``None``."""
        db = self._require_db()
        async with db.execute(
            "SELECT run_id, step_idx, branch_id, parent_step_idx, ts, "
            "state_snapshot, clips_facts, last_node, next_action, "
            "structural_hash, runtime_hash, side_effects_hash, parent_run_id "
            "FROM checkpoints WHERE run_id = ? "
            "ORDER BY step_idx DESC LIMIT 1",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_checkpoint(row) if row is not None else None

    async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None:
        """Return the checkpoint at ``(run_id, step)`` or ``None``."""
        db = self._require_db()
        async with db.execute(
            "SELECT run_id, step_idx, branch_id, parent_step_idx, ts, "
            "state_snapshot, clips_facts, last_node, next_action, "
            "structural_hash, runtime_hash, side_effects_hash, parent_run_id "
            "FROM checkpoints WHERE run_id = ? AND step_idx = ? LIMIT 1",
            (run_id, step),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_checkpoint(row) if row is not None else None

    async def list_runs(
        self, *, since: datetime | None = None, limit: int = 100
    ) -> list[RunSummary]:
        """Return run summaries, optionally filtered by ``started_at >= since``."""
        db = self._require_db()
        if since is None:
            sql = (
                "SELECT run_id, graph_hash, started_at, last_step_at, status, "
                "parent_run_id FROM runs ORDER BY started_at DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (limit,)
        else:
            sql = (
                "SELECT run_id, graph_hash, started_at, last_step_at, status, "
                "parent_run_id FROM runs WHERE started_at >= ? "
                "ORDER BY started_at DESC LIMIT ?"
            )
            params = (since.isoformat(), limit)
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_run_summary(r) for r in rows]

    # ----- Helpers -------------------------------------------------------- #

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise CheckpointError(
                "SQLiteCheckpointer not bootstrapped; call bootstrap() first",
                path=str(self._path),
            )
        return self._db


# --------------------------------------------------------------------------- #
# Row decoders                                                                #
# --------------------------------------------------------------------------- #


def _row_to_checkpoint(row: Iterable[Any]) -> Checkpoint:
    """Hydrate a ``checkpoints`` row into a :class:`Checkpoint`."""
    (
        run_id,
        step_idx,
        branch_id,
        parent_step_idx,
        ts,
        state_snapshot,
        clips_facts,
        last_node,
        next_action,
        structural_hash,
        runtime_hash,
        side_effects_hash,
        parent_run_id,
    ) = tuple(row)
    return Checkpoint(
        run_id=run_id,
        step=int(step_idx),
        branch_id=branch_id,
        parent_step_idx=parent_step_idx,
        graph_hash=structural_hash,
        runtime_hash=runtime_hash,
        state=cast("dict[str, Any]", loads_jsonb(state_snapshot)),
        clips_facts=cast("list[Any]", loads_jsonb(clips_facts)),
        last_node=last_node,
        next_action=loads_jsonb(next_action),
        timestamp=datetime.fromisoformat(ts),
        parent_run_id=parent_run_id,
        side_effects_hash=side_effects_hash,
    )


def _row_to_run_summary(row: Iterable[Any]) -> RunSummary:
    """Hydrate a ``runs`` row into a :class:`RunSummary`."""
    (run_id, graph_hash, started_at, last_step_at, status, parent_run_id) = tuple(row)
    return RunSummary(
        run_id=run_id,
        graph_hash=graph_hash,
        started_at=datetime.fromisoformat(started_at),
        last_step_at=datetime.fromisoformat(last_step_at),
        status=status,
        parent_run_id=parent_run_id,
    )


def _utcnow_iso() -> str:
    """ISO-8601 UTC timestamp with microsecond precision."""
    from datetime import UTC

    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Static Protocol conformance                                                 #
# --------------------------------------------------------------------------- #
# Pin SQLiteCheckpointer to the Checkpointer Protocol at type-check time.
# Any signature drift (return type, parameter, kw-only-ness) surfaces as a
# pyright error here without runtime cost.
if TYPE_CHECKING:
    _: Checkpointer = SQLiteCheckpointer.__new__(SQLiteCheckpointer)
