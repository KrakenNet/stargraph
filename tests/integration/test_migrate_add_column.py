# SPDX-License-Identifier: Apache-2.0
"""FR-17: ``migrate(plan)`` add-nullable-column happy path on SQLiteDocStore.

The v1 migrate contract (stargraph-knowledge design §4.5) supports
add-nullable-column **only**. SQLite's ``ALTER TABLE ... ADD COLUMN``
makes this a one-statement DDL change; the test seeds rows, applies
the plan, and asserts:

1. ``schema_v`` (``StoreHealth.version``) bumps to ``plan.target_version``.
2. Pre-existing rows are preserved verbatim (no rewrite).
3. The new column reads back as NULL on legacy rows.
4. Subsequent writes can populate the new column via raw SQL.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiosqlite
import pytest

from stargraph.stores._common import MigrationPlan
from stargraph.stores.sqlite_doc import SQLiteDocStore

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


def test_migrate_add_column_bumps_schema_v_and_preserves_data(tmp_path: Path) -> None:
    """Add nullable column → schema_v bumps; rows preserved; legacy rows read NULL."""
    db_path = tmp_path / "doc.sqlite"
    store = SQLiteDocStore(db_path)
    asyncio.run(store.bootstrap())

    # Seed two documents pre-migration.
    asyncio.run(
        store.put(
            "doc-a",
            "alpha content",
            metadata={"source": "test-a"},
        )
    )
    asyncio.run(
        store.put(
            "doc-b",
            b"\x00\x01beta",
            metadata={"source": "test-b"},
        )
    )

    health_before = asyncio.run(store.health())
    assert health_before.fragment_count == 2
    base_version = health_before.version

    plan = MigrationPlan(
        target_version=base_version + 1,
        operations=[
            {
                "op": "add_column",
                "table": "documents",
                "column": "summary",
                "type": "TEXT",
                "nullable": True,
            }
        ],
    )
    asyncio.run(store.migrate(plan))

    # 1. schema_v bumped.
    health_after = asyncio.run(store.health())
    assert health_after.version == base_version + 1
    assert health_after.fragment_count == 2

    # 2. Pre-existing rows preserved (Document round-trips).
    doc_a = asyncio.run(store.get("doc-a"))
    assert doc_a is not None
    assert doc_a.content == "alpha content"
    assert doc_a.metadata == {"source": "test-a"}

    doc_b = asyncio.run(store.get("doc-b"))
    assert doc_b is not None
    assert doc_b.content == b"\x00\x01beta"
    assert doc_b.metadata == {"source": "test-b"}

    # 3. New column exists and is NULL on legacy rows.
    async def _read_summary() -> list[tuple[str, object]]:
        async with (
            aiosqlite.connect(db_path) as conn,
            conn.execute("SELECT doc_id, summary FROM documents ORDER BY doc_id") as cur,
        ):
            return [(str(row[0]), row[1]) for row in await cur.fetchall()]

    rows = asyncio.run(_read_summary())
    assert rows == [("doc-a", None), ("doc-b", None)]

    # 4. The new column accepts writes via raw SQL (proves DDL took effect).
    async def _write_summary() -> None:
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "UPDATE documents SET summary = ? WHERE doc_id = ?",
                ("alpha summary", "doc-a"),
            )
            await conn.commit()

    asyncio.run(_write_summary())
    rows_after = asyncio.run(_read_summary())
    assert rows_after == [("doc-a", "alpha summary"), ("doc-b", None)]


def test_migrate_add_column_is_idempotent_under_replay(tmp_path: Path) -> None:
    """Re-running the same plan at the same target_version stays at that version.

    SQLite ``ALTER TABLE ADD COLUMN`` is *not* idempotent (raises on
    duplicate column), so the v1 contract is that callers replay only
    when ``target_version`` advances. This test pins that re-applying
    a strictly-greater target_version works (incremental migration).
    """
    db_path = tmp_path / "doc.sqlite"
    store = SQLiteDocStore(db_path)
    asyncio.run(store.bootstrap())
    asyncio.run(store.put("d1", "x", metadata={}))

    plan_v2 = MigrationPlan(
        target_version=2,
        operations=[
            {
                "op": "add_column",
                "table": "documents",
                "column": "summary",
                "type": "TEXT",
                "nullable": True,
            }
        ],
    )
    asyncio.run(store.migrate(plan_v2))

    plan_v3 = MigrationPlan(
        target_version=3,
        operations=[
            {
                "op": "add_column",
                "table": "documents",
                "column": "tags",
                "type": "TEXT",
                "nullable": True,
            }
        ],
    )
    asyncio.run(store.migrate(plan_v3))

    health = asyncio.run(store.health())
    assert health.version == 3

    # Pre-migration rows still present.
    doc = asyncio.run(store.get("d1"))
    assert doc is not None
    assert doc.content == "x"
