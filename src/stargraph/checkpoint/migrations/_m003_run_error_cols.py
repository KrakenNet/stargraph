# SPDX-License-Identifier: Apache-2.0
"""Migration 3 -- run-failure diagnostics columns (#68).

Adds ``error_class`` + ``error_cause`` to ``runs_history`` so a failed
run records *why* it failed: a node exception is distinguishable from a
HITL interrupt timeout (both otherwise read as a bare terminal status
with no cause). ``error_class`` is the exception type name; ``error_cause``
is the message (``str(exc)``). Both are nullable and populated only on
the error/failed path -- successful runs leave them ``NULL``.

SQLite ``ALTER TABLE ... ADD COLUMN`` appends nullable columns in place
without rewriting existing rows, so this is safe on populated databases
(old rows get ``NULL`` for both). The columns are added at the end of the
table; all reads use explicit column lists (never ``SELECT *``), so
physical column order does not matter.

Idempotent: ``ADD COLUMN`` errors with "duplicate column name" if the
column already exists, so each statement is guarded -- the wider runner
also skips already-applied migrations via ``stargraph_schema_version``,
but the guard keeps this safe under a direct re-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

__all__ = ["up"]


_ADD_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE runs_history ADD COLUMN error_class TEXT",
    "ALTER TABLE runs_history ADD COLUMN error_cause TEXT",
)


async def up(db: aiosqlite.Connection) -> None:
    """Apply migration 3 to ``db``. Idempotent (guards duplicate columns)."""
    import aiosqlite

    for stmt in _ADD_COLUMNS:
        try:
            await db.execute(stmt)
        except aiosqlite.OperationalError as exc:  # pragma: no cover - re-run guard
            if "duplicate column name" not in str(exc).lower():
                raise
    await db.commit()
