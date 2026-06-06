# SPDX-License-Identifier: Apache-2.0
"""Shared SQLite plumbing for ``stargraph.stores`` drivers (FR-13, design §3.3-§3.5).

The Phase 1 stores (``SQLiteDocStore``, ``SQLiteFactStore``, ``SQLiteEmbeddingStore``,
``SQLiteGraphStore``) all open an aiosqlite connection with the same WAL pragmas
and use a per-database ``_migrations`` table to track which schema versions have
been applied. This module factors out the three primitives every driver needs:

* :func:`_apply_pragmas` — mirror the checkpointer's WAL settings (design §3.2.3).
* :func:`_ensure_migrations_table` — idempotent create of the
  ``_migrations(version, applied_at)`` ledger.
* :func:`_apply_migrations` — replay any unapplied callables in version order.

JSONB columns are encoded with the orjson helpers re-exported from
:mod:`stargraph.checkpoint._codec` so the doc/fact/graph stores stay byte-compatible
with the checkpointer's BLOB layout.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stargraph.checkpoint._codec import dumps_jsonb, loads_jsonb

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import aiosqlite

__all__ = [
    "_apply_migrations",
    "_apply_pragmas",
    "_ensure_migrations_table",
    "dumps_jsonb",
    "loads_jsonb",
]


async def _apply_pragmas(conn: aiosqlite.Connection) -> None:
    """Apply Stargraph's standard SQLite pragmas (design §3.2.3).

    ``journal_mode=WAL`` enables concurrent readers; ``synchronous=NORMAL``
    is the WAL-mode default that trades the last-fsync window for throughput;
    ``busy_timeout=5000`` lets writers wait 5s on lock contention before
    erroring; ``foreign_keys=ON`` is opt-in per SQLite connection.
    """
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.commit()


async def _ensure_migrations_table(conn: aiosqlite.Connection) -> None:
    """Create the ``_migrations`` ledger if missing (idempotent)."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "  version    INTEGER PRIMARY KEY,"
        "  applied_at TEXT    NOT NULL"
        ")"
    )
    await conn.commit()


async def _apply_migrations(
    conn: aiosqlite.Connection,
    migrations: list[Callable[[aiosqlite.Connection], Awaitable[None]]],
) -> None:
    """Run any migrations whose 1-based index exceeds the highest applied version.

    Each callable is awaited with the live connection and is responsible for
    its own DDL. After it returns, the runner records the new version and
    commits. The ledger is created on first call if absent.
    """
    await _ensure_migrations_table(conn)

    async with conn.execute("SELECT COALESCE(MAX(version), 0) FROM _migrations") as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row is not None else 0

    for idx, migration in enumerate(migrations, start=1):
        if idx <= current:
            continue
        await migration(conn)
        await conn.execute(
            "INSERT OR IGNORE INTO _migrations (version, applied_at) VALUES (?, ?)",
            (idx, datetime.now(UTC).isoformat()),
        )
        await conn.commit()
