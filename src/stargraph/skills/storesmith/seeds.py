# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed stores — the trainset cold start.

Each entry is a verified ``(brief → store + test)`` pair: a single self-contained
``DocStore`` class distilled from ``stargraph.stores.sqlite_doc.SQLiteDocStore``
(pure aiosqlite, fully offline-gateable) paired with a pytest test and a fixture.
Seed 1 covers a plain text put/get/query round-trip with simple metadata; seed 2
covers rich mixed-type metadata plus a ``query`` filter. They give RAG retrieval
and few-shot compile something to stand on before the generator has produced
anything. ``id`` is a fixed literal so ``seed_trainset`` is idempotent across runs.

``tests/integration/storesmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any

# A single self-contained DocStore, distilled from SQLiteDocStore. Pure aiosqlite
# + stdlib + the shared store contract types — no other stargraph store internals,
# so it imports and runs under the offline contract gate.
_DOC_STORE_SOURCE = '''\
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from stargraph.errors import MigrationNotSupported
from stargraph.stores._common import MigrationPlan, StoreHealth, _validate_migration_plan
from stargraph.stores.doc import Document

_DDL = (
    "CREATE TABLE IF NOT EXISTS documents ("
    "  doc_id     TEXT PRIMARY KEY,"
    "  content    BLOB NOT NULL,"
    "  is_text    INTEGER NOT NULL,"
    "  metadata   BLOB NOT NULL,"
    "  created_at TEXT NOT NULL"
    ")"
)


class SqliteDocStore:
    """Minimal sqlite-backed DocStore: put/get/query + add-column migrate guard."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def bootstrap(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS _migrations ("
                "  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            await conn.execute(_DDL)
            await conn.commit()

    async def health(self) -> StoreHealth:
        async with aiosqlite.connect(self._path) as conn:
            async with conn.execute("SELECT COUNT(*) FROM documents") as cur:
                row = await cur.fetchone()
            count = int(row[0]) if row is not None else 0
            async with conn.execute(
                "SELECT COALESCE(MAX(version), 1) FROM _migrations"
            ) as cur:
                vrow = await cur.fetchone()
            version = int(vrow[0]) if vrow is not None else 1
        return StoreHealth(ok=True, version=version, fragment_count=count,
                           fs_type="unknown", lock_state="free")

    async def migrate(self, plan: MigrationPlan) -> None:
        _validate_migration_plan(plan, store="sqlite_doc")
        async with aiosqlite.connect(self._path) as conn:
            for op in plan.operations:
                table = op.get("table")
                column = op.get("column")
                col_type = op.get("type", "TEXT")
                if not isinstance(table, str) or not isinstance(column, str):
                    raise MigrationNotSupported(
                        "add_column requires string table and column",
                        store="sqlite_doc", operation="add_column", reason="missing-fields",
                    )
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            await conn.execute(
                "INSERT OR REPLACE INTO _migrations (version, applied_at) VALUES (?, ?)",
                (plan.target_version, datetime.now(UTC).isoformat()),
            )
            await conn.commit()

    async def put(self, doc_id: str, content: str | bytes, *,
                  metadata: dict[str, Any] | None = None) -> None:
        if isinstance(content, str):
            blob: bytes = content.encode("utf-8")
            is_text = 1
        else:
            blob = content
            is_text = 0
        meta_blob = json.dumps(metadata or {}).encode("utf-8")
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO documents"
                " (doc_id, content, is_text, metadata, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (doc_id, blob, is_text, meta_blob, datetime.now(UTC).isoformat()),
            )
            await conn.commit()

    async def get(self, doc_id: str) -> Document | None:
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(
                "SELECT doc_id, content, is_text, metadata, created_at"
                " FROM documents WHERE doc_id = ?",
                (doc_id,),
            ) as cur,
        ):
            row = await cur.fetchone()
        return _to_doc(row) if row is not None else None

    async def query(self, filter: str | None = None, *, limit: int = 100) -> list[Document]:
        sql = "SELECT doc_id, content, is_text, metadata, created_at FROM documents"
        if filter:
            sql += f" WHERE {filter}"
        sql += " LIMIT ?"
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(sql, (limit,)) as cur,
        ):
            rows = await cur.fetchall()
        return [_to_doc(row) for row in rows]


def _to_doc(row: Any) -> Document:
    doc_id, content_blob, is_text, metadata_blob, created_at = row
    content: str | bytes = (
        bytes(content_blob).decode("utf-8") if int(is_text) == 1 else bytes(content_blob)
    )
    metadata = json.loads(bytes(metadata_blob).decode("utf-8"))
    return Document(
        id=str(doc_id),
        content=content,
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=datetime.fromisoformat(str(created_at)),
    )
'''


def _pair(
    seed_id: str,
    brief: str,
    class_name: str,
    fixture: dict[str, Any],
    store_source: str,
    test_source: str,
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "class_name": class_name,
        "fixture": fixture,
        "store_source": store_source,
        "test_source": test_source,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "80010000001",
        "a sqlite DocStore that round-trips plain text content and metadata by doc id",
        "SqliteDocStore",
        {
            "doc_id": "doc-1",
            "content": "hello world",
            "content2": "updated body",
            "metadata": {"source": "seed", "n": 1},
        },
        _DOC_STORE_SOURCE,
        """\
import asyncio
from pathlib import Path

from store import SqliteDocStore


def test_text_roundtrip(tmp_path: Path) -> None:
    store = SqliteDocStore(tmp_path / "docs.db")

    async def _run() -> None:
        await store.bootstrap()
        await store.put("doc-1", "hello world", metadata={"source": "seed", "n": 1})
        got = await store.get("doc-1")
        assert got is not None
        assert got.content == "hello world"
        assert got.metadata == {"source": "seed", "n": 1}
        rows = await store.query()
        assert any(r.id == "doc-1" for r in rows)
        assert await store.get("missing") is None

    asyncio.run(_run())
""",
    ),
    _pair(
        "80010000002",
        "a sqlite DocStore with rich mixed-type metadata and a SQL query filter on doc id",
        "SqliteDocStore",
        {
            "doc_id": "report-42",
            "content": "first revision",
            "content2": "second revision",
            "metadata": {"tag": "ops", "score": 7, "active": True, "nested": {"k": "v"}},
        },
        _DOC_STORE_SOURCE,
        """\
import asyncio
from pathlib import Path

from store import SqliteDocStore


def test_metadata_and_filter(tmp_path: Path) -> None:
    store = SqliteDocStore(tmp_path / "docs.db")
    meta = {"tag": "ops", "score": 7, "active": True, "nested": {"k": "v"}}

    async def _run() -> None:
        await store.bootstrap()
        await store.put("report-42", "first revision", metadata=meta)
        await store.put("other", "noise")
        got = await store.get("report-42")
        assert got is not None
        assert got.metadata == meta
        hits = await store.query("doc_id = 'report-42'")
        assert len(hits) == 1
        assert hits[0].id == "report-42"
        await store.put("report-42", "second revision")
        again = await store.get("report-42")
        assert again is not None
        assert again.content == "second revision"

    asyncio.run(_run())
""",
    ),
]
