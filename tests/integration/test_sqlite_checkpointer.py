# SPDX-License-Identifier: Apache-2.0
"""TDD-RED suite for the SQLite checkpointer driver (FR-17, design §3.2.3).

These tests pin the contract for ``stargraph.checkpoint.sqlite.SQLiteCheckpointer``
*before* the implementation lands in task 1.21. They MUST be RED -- the
``stargraph.checkpoint.sqlite`` module does not exist yet, so every case fails
with :class:`ModuleNotFoundError`/:class:`ImportError` at the deferred
import. That is the expected RED state for this task.

Cases (per task 1.20):

1. ``test_bootstrap_sets_wal_pragmas``      -- ``journal_mode=WAL``,
   ``synchronous=NORMAL``, ``busy_timeout=5000`` after :py:meth:`bootstrap`.
2. ``test_bootstrap_is_idempotent``         -- second :py:meth:`bootstrap`
   on the same path is a no-op (no error).
3. ``test_bootstrap_rejects_network_fs``    -- prefix-matched paths
   (``/mnt/``, ``//host``, ``\\\\host``, ``/Volumes/.+SMB``) raise
   :class:`stargraph.errors.CheckpointError` carrying ``reason='network-fs'``.
4. ``test_write_then_read_latest_round_trip`` -- :py:meth:`write` followed by
   :py:meth:`read_latest` returns the same :class:`Checkpoint` payload.
5. ``test_sqlite_multi_process_writer_refusal`` -- a foreign process holding
   ``fcntl.flock(fd, LOCK_EX)`` on the DB file makes :py:meth:`bootstrap`
   raise :class:`CheckpointError` matching ``r'multi-process writer.*not
   supported'``. POSIX-only (skipped on win32; ``msvcrt.locking`` semantics
   differ, separate Windows path may follow).

The deferred import pattern (`from stargraph.checkpoint.sqlite import ...` inside
each test, guarded by ``# pyright: ignore[reportMissingImports]``) keeps
pyright + ruff green during RED while ensuring runtime collection still
fails with ``ImportError`` (the RED signal).
"""

from __future__ import annotations

import asyncio
import multiprocessing
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from stargraph.checkpoint import Checkpoint
from stargraph.errors import CheckpointError

if TYPE_CHECKING:
    from collections.abc import Iterator


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _import_sqlite_checkpointer() -> Any:
    """Return the (not-yet-implemented) ``SQLiteCheckpointer`` class.

    Lives behind a helper so the import is expressed once and the missing-
    module / unknown-symbol cascade is suppressed in a single place. Until
    task 1.21 lands, calling this raises :class:`ImportError` (the RED state).
    """
    import importlib

    mod = importlib.import_module("stargraph.checkpoint.sqlite")
    return mod.SQLiteCheckpointer


def _make_checkpoint(run_id: str = "run-001", step: int = 0) -> Checkpoint:
    """Build a fully-populated :class:`Checkpoint` for round-trip tests."""
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="sha256:graph",
        runtime_hash="sha256:runtime",
        state={"x": 1},
        clips_facts=[{"template": "evidence", "slots": {"field": "v"}}],
        last_node="n0",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side",
    )


def _hold_exclusive_flock(path: str, ready_path: str, hold_seconds: float) -> None:
    """Child-process target: open ``path`` and hold ``fcntl.flock(LOCK_EX)``.

    Writes a sentinel file (``ready_path``) once the lock is acquired so the
    parent can synchronize before attempting :py:meth:`bootstrap`. The lock
    is released when the process exits (after ``hold_seconds``).
    """
    import fcntl  # POSIX-only; wrapper test guards with skipif(win32)

    fd = open(path, "a+b")  # noqa: SIM115 -- intentional long-lived handle
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        Path(ready_path).touch()
        time.sleep(hold_seconds)
    finally:
        fd.close()


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[Path]:
    """Local-FS SQLite DB path for happy-path tests."""
    yield tmp_path / "checkpoints.db"


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


async def test_bootstrap_sets_wal_pragmas(db_path: Path) -> None:
    """After ``bootstrap``, the three WAL pragmas are set per design §3.2.3.

    ``journal_mode=WAL`` is a database-level pragma and persists to disk;
    ``synchronous`` and ``busy_timeout`` are *connection-level* in SQLite
    (each new ``sqlite3_open`` resets them to defaults — FULL=2 and a
    build-time-default busy timeout). The contract here is therefore:

    * journal_mode is checked on a fresh ``aiosqlite.connect`` (persistent).
    * synchronous + busy_timeout are checked on the writer connection the
      :class:`SQLiteCheckpointer` itself owns (where they actually live).
    """
    sqlite_checkpointer_cls = _import_sqlite_checkpointer()

    cp: Any = sqlite_checkpointer_cls(db_path)
    await cp.bootstrap()

    import aiosqlite

    # journal_mode persists at the database level.
    async with aiosqlite.connect(db_path) as db, db.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
        assert row is not None
        assert str(row[0]).lower() == "wal"

    # synchronous + busy_timeout live on the writer's own connection.
    writer_db: Any = cp._db
    async with writer_db.execute("PRAGMA synchronous") as cur:
        row = await cur.fetchone()
        assert row is not None
        # synchronous=NORMAL → integer 1
        assert int(row[0]) == 1

    async with writer_db.execute("PRAGMA busy_timeout") as cur:
        row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == 5000


async def test_bootstrap_is_idempotent(db_path: Path) -> None:
    """Re-running ``bootstrap`` on an already-migrated DB is a no-op."""
    sqlite_checkpointer_cls = _import_sqlite_checkpointer()

    cp: Any = sqlite_checkpointer_cls(db_path)
    await cp.bootstrap()
    # Second call must NOT raise.
    await cp.bootstrap()


@pytest.mark.parametrize(
    "bad_path",
    [
        "/mnt/share/checkpoints.db",
        "//host/share/checkpoints.db",
        "\\\\host\\share\\checkpoints.db",
        "/Volumes/MySMBShare/checkpoints.db",
    ],
)
async def test_bootstrap_rejects_network_fs(bad_path: str) -> None:
    """Network-FS prefixes raise ``CheckpointError(reason='network-fs')``."""
    sqlite_checkpointer_cls = _import_sqlite_checkpointer()

    cp: Any = sqlite_checkpointer_cls(Path(bad_path))
    with pytest.raises(CheckpointError) as excinfo:
        await cp.bootstrap()
    assert excinfo.value.context.get("reason") == "network-fs"


async def test_write_then_read_latest_round_trip(db_path: Path) -> None:
    """``write(checkpoint)`` then ``read_latest(run_id)`` returns equal payload."""
    sqlite_checkpointer_cls = _import_sqlite_checkpointer()

    cp: Any = sqlite_checkpointer_cls(db_path)
    await cp.bootstrap()

    original = _make_checkpoint(run_id="run-rt", step=3)
    await cp.write(original)

    fetched = await cp.read_latest("run-rt")
    assert fetched is not None
    assert fetched == original


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "POSIX fcntl.flock; Windows uses msvcrt.locking with different "
        "semantics — separate Windows test path may follow"
    ),
)
async def test_sqlite_multi_process_writer_refusal(tmp_path: Path) -> None:
    """A foreign LOCK_EX holder makes ``bootstrap`` raise CheckpointError.

    Spawns a child process that opens the DB file and acquires
    ``fcntl.flock(LOCK_EX)``; once the child signals ready (sentinel file),
    the parent attempts :py:meth:`bootstrap` and expects refusal.
    """
    sqlite_checkpointer_cls = _import_sqlite_checkpointer()

    db_file = tmp_path / "lock.db"
    db_file.touch()
    ready_file = tmp_path / "child.ready"

    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(
        target=_hold_exclusive_flock,
        args=(str(db_file), str(ready_file), 10.0),
    )
    child.start()
    try:
        # Wait for the child to acquire the lock (bounded poll, no infinite
        # spin). Signal is a sentinel file written by another OS process, so
        # the asyncio.Event guidance (ASYNC110) does not apply -- there is no
        # in-process awaitable to wait on.
        deadline = time.monotonic() + 5.0
        while not ready_file.exists() and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        assert ready_file.exists(), "child failed to acquire LOCK_EX in time"

        cp: Any = sqlite_checkpointer_cls(db_file)
        with pytest.raises(CheckpointError) as excinfo:
            await cp.bootstrap()
        assert re.search(
            r"multi-process writer.*not supported",
            str(excinfo.value),
        ), f"unexpected message: {excinfo.value}"
    finally:
        child.terminate()
        child.join(timeout=5.0)
