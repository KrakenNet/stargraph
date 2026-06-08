# SPDX-License-Identifier: Apache-2.0
"""Regression (#68): ``runs_history`` records *why* a run failed.

A failed run must persist the failure reason so a node exception is
distinguishable from a HITL interrupt timeout (both otherwise read as a
bare terminal status). Migration 003 adds nullable ``error_class`` +
``error_cause`` columns; :class:`~stargraph.serve.history.RunHistory`
writes them on the ``error``/``failed`` path and reads them back.

Two slices:

1. **Persistence + read-back** over a real bootstrapped SQLite DB: an
   ``error`` update populates both columns; a ``done`` update leaves them
   ``NULL``.
2. **Migration 003 in isolation**: applied to a pre-#68 ``runs_history``
   table it adds the two columns (legacy rows read ``NULL``) and is
   idempotent under a direct re-run.

Refs: design §6.5 (runs_history), issue #68.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
import pytest

from stargraph.checkpoint.migrations._m003_run_error_cols import up as migration_003
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.serve.history import RunHistory

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.serve, pytest.mark.integration]


async def test_run_history_persists_error_reason(tmp_path: Path) -> None:
    """An ``error`` update records ``error_class`` + ``error_cause``; ``done`` leaves NULL (#68)."""
    checkpointer = SQLiteCheckpointer(tmp_path / "history.sqlite")
    await checkpointer.bootstrap()
    try:
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        assert db is not None
        history = RunHistory(db)
        await history.bootstrap()

        now = datetime.now(UTC)

        # Failed run -- both diagnostic columns populated.
        await history.insert_pending("run-error", "graph-hash", "manual")
        await history.update_status(
            "run-error",
            "error",
            finished_at=now,
            duration_ms=12,
            error_class="ValueError",
            error_cause="boom: bad input",
        )
        rec = await history.get("run-error")
        assert rec is not None
        assert rec.status == "error"
        assert rec.error_class == "ValueError"
        assert rec.error_cause == "boom: bad input"
        assert rec.duration_ms == 12

        # Successful run -- diagnostic columns stay NULL.
        await history.insert_pending("run-done", "graph-hash", "manual")
        await history.update_status("run-done", "done", finished_at=now, duration_ms=5)
        rec_done = await history.get("run-done")
        assert rec_done is not None
        assert rec_done.status == "done"
        assert rec_done.error_class is None
        assert rec_done.error_cause is None
    finally:
        await checkpointer.close()


async def test_migration_003_adds_error_columns_idempotently(tmp_path: Path) -> None:
    """Migration 003 ALTERs a pre-#68 table; legacy rows read NULL; re-run is a no-op (#68)."""
    db = await aiosqlite.connect(tmp_path / "legacy.sqlite")
    try:
        # Pre-#68 runs_history shape: no error_class / error_cause columns.
        await db.execute(
            "CREATE TABLE runs_history (run_id TEXT PRIMARY KEY, status TEXT)"
        )
        await db.execute(
            "INSERT INTO runs_history (run_id, status) VALUES ('legacy', 'done')"
        )
        await db.commit()

        await migration_003(db)

        # Columns now exist; the legacy row reads NULL for both.
        async with db.execute(
            "SELECT error_class, error_cause FROM runs_history WHERE run_id = 'legacy'"
        ) as cur:
            row = await cur.fetchone()
        assert row == (None, None)

        # Idempotent: a direct re-run swallows the duplicate-column error.
        await migration_003(db)
    finally:
        await db.close()
