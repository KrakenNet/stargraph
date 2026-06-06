# SPDX-License-Identifier: Apache-2.0
"""Integration tests for :class:`stargraph.stores.sqlite_doc.SQLiteDocStore` (FR-4, FR-13).

Covers the three observable contracts task 3.20 calls out:

1. ``test_bootstrap_pragmas`` -- after ``bootstrap``, ``PRAGMA journal_mode``
   reports ``wal`` (the database-level pragma persists to disk; per-connection
   pragmas are exercised in the checkpointer suite).
2. ``test_crud_roundtrip`` -- ``put`` then ``get`` then ``query`` returns
   the same content; after ``unpin``-equivalent semantics (here: replacing
   doc with new payload via ``put``), reads reflect the latest write.
3. ``test_metadata_orjson_roundtrip`` -- mixed ``str``/``int`` metadata
   round-trips bit-identical through the orjson JSONB column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from stargraph.stores.sqlite_doc import SQLiteDocStore

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


async def test_bootstrap_pragmas(tmp_path: Path) -> None:
    """``bootstrap`` enables WAL journal mode on the underlying SQLite file."""
    store = SQLiteDocStore(tmp_path / "docs.db")
    await store.bootstrap()

    async with (
        aiosqlite.connect(tmp_path / "docs.db") as conn,
        conn.execute("PRAGMA journal_mode") as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert str(row[0]).lower() == "wal"


async def test_crud_roundtrip(tmp_path: Path) -> None:
    """``put`` -> ``get`` -> ``query`` round-trips; replace via ``put`` overwrites."""
    store = SQLiteDocStore(tmp_path / "docs.db")
    await store.bootstrap()

    await store.put("doc-1", "hello world")
    got = await store.get("doc-1")
    assert got is not None
    assert got.id == "doc-1"
    assert got.content == "hello world"

    rows = await store.query()
    assert len(rows) == 1
    assert rows[0].id == "doc-1"

    # Replace via INSERT OR REPLACE semantics.
    await store.put("doc-1", "updated")
    after = await store.get("doc-1")
    assert after is not None
    assert after.content == "updated"


async def test_metadata_orjson_roundtrip(tmp_path: Path) -> None:
    """Mixed-type metadata round-trips identically through the JSONB column."""
    store = SQLiteDocStore(tmp_path / "docs.db")
    await store.bootstrap()

    metadata = {"k": "v", "n": 1}
    await store.put("doc-1", "payload", metadata=metadata)

    got = await store.get("doc-1")
    assert got is not None
    assert got.metadata == metadata
