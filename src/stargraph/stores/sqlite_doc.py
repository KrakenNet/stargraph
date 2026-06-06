# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed :class:`DocStore` provider (FR-4, FR-13, design §3.3).

POC scope (Phase 1, task 1.16): ``bootstrap`` creates the ``documents``
table; ``put`` / ``get`` / ``query`` round-trip text/bytes content with
orjson-JSONB metadata. Single-writer-per-path serialization is enforced
through :func:`stargraph.stores._common._lock_for` (FR-9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import Any, cast

import aiosqlite

from stargraph.errors import MigrationNotSupported
from stargraph.stores._common import (
    MigrationPlan,
    StoreHealth,
    _detect_fs_type,  # pyright: ignore[reportPrivateUsage]
    _lock_for,  # pyright: ignore[reportPrivateUsage]
    _nfs_warning,  # pyright: ignore[reportPrivateUsage]
    _validate_migration_plan,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.stores._sqlite_base import (
    _apply_pragmas,
    _ensure_migrations_table,
    dumps_jsonb,
    loads_jsonb,
)
from stargraph.stores.doc import Document

__all__ = ["SQLiteDocStore"]


_BOOTSTRAP_DDL = (
    "CREATE TABLE IF NOT EXISTS documents ("
    "  doc_id     TEXT PRIMARY KEY,"
    "  content    BLOB NOT NULL,"
    "  is_text    INTEGER NOT NULL,"
    "  metadata   BLOB NOT NULL,"
    "  created_at TEXT NOT NULL"
    ")"
)


class SQLiteDocStore:
    """SQLite ``DocStore`` (design §3.3) -- POC put/get/query.

    Content is persisted as a BLOB; the ``is_text`` flag records whether
    the original payload was :class:`str` so :meth:`get` / :meth:`query`
    can restore the exact Python type. Metadata round-trips through
    :func:`dumps_jsonb` / :func:`loads_jsonb`.
    """

    def __init__(self, path: Path) -> None:
        """Create a doc store rooted at ``path`` (file is created on bootstrap)."""
        self._path = path

    async def bootstrap(self) -> None:
        """Idempotent schema bootstrap (creates ``documents`` table)."""
        async with _lock_for(self._path):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._path) as conn:
                await _apply_pragmas(conn)
                await _ensure_migrations_table(conn)
                await conn.execute(_BOOTSTRAP_DDL)
                await conn.commit()

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (POC: minimal fields)."""
        async with aiosqlite.connect(self._path) as conn:
            async with conn.execute("SELECT COUNT(*) FROM documents") as cur:
                row = await cur.fetchone()
            count = int(row[0]) if row is not None else 0
            async with conn.execute("SELECT COALESCE(MAX(version), 1) FROM _migrations") as cur:
                vrow = await cur.fetchone()
            version = int(vrow[0]) if vrow is not None else 1
        fs_type = _detect_fs_type(self._path)
        warnings: list[str] = []
        nfs_warning = _nfs_warning(fs_type)
        if nfs_warning is not None:
            warnings.append(nfs_warning)
        return StoreHealth(
            ok=True,
            version=version,
            fragment_count=count,
            fs_type=fs_type,
            lock_state="free",
            warnings=warnings,
        )

    async def migrate(self, plan: MigrationPlan) -> None:
        """Apply ``plan`` to the ``documents`` table (FR-17 add-column only).

        Rejects type narrows / renames / drops via
        :func:`_validate_migration_plan`. For each ``add_column`` op,
        runs ``ALTER TABLE ... ADD COLUMN`` and records the bumped
        ``schema_v`` in the ``_migrations`` ledger so subsequent
        ``health()`` callers observe the new ``version``.
        """
        _validate_migration_plan(plan, store="sqlite_doc")
        async with _lock_for(self._path), aiosqlite.connect(self._path) as conn:
            await _apply_pragmas(conn)
            await _ensure_migrations_table(conn)
            for op in plan.operations:
                table = op.get("table")
                column = op.get("column")
                col_type = op.get("type", "TEXT")
                if not isinstance(table, str) or not isinstance(column, str):
                    raise MigrationNotSupported(
                        "add_column requires string 'table' and 'column'",
                        store="sqlite_doc",
                        operation="add_column",
                        reason="missing-fields",
                    )
                # SQLite ALTER TABLE ADD COLUMN is always nullable unless
                # a NOT NULL DEFAULT is given; we validate nullable=True
                # in _validate_migration_plan, so the bare form is safe.
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            await conn.execute(
                "INSERT OR REPLACE INTO _migrations (version, applied_at) VALUES (?, ?)",
                (plan.target_version, datetime.now(UTC).isoformat()),
            )
            await conn.commit()

    async def put(
        self,
        doc_id: str,
        content: str | bytes,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert-or-replace document ``doc_id`` with ``content`` and ``metadata``."""
        if isinstance(content, str):
            blob = content.encode("utf-8")
            is_text = 1
        else:
            blob = content
            is_text = 0
        meta_blob = dumps_jsonb(metadata or {})
        created_at = datetime.now(UTC).isoformat()
        async with _lock_for(self._path), aiosqlite.connect(self._path) as conn:
            await _apply_pragmas(conn)
            await conn.execute(
                "INSERT OR REPLACE INTO documents"
                " (doc_id, content, is_text, metadata, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (doc_id, blob, is_text, meta_blob, created_at),
            )
            await conn.commit()

    async def get(self, doc_id: str) -> Document | None:
        """Return the :class:`Document` for ``doc_id`` or ``None`` if absent."""
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(
                "SELECT doc_id, content, is_text, metadata, created_at"
                " FROM documents WHERE doc_id = ?",
                (doc_id,),
            ) as cur,
        ):
            row = await cur.fetchone()
        if row is None:
            return None
        return _row_to_document(row)

    async def query(
        self,
        filter: str | None = None,  # noqa: A002
        *,
        limit: int = 100,
    ) -> list[Document]:
        """Return up to ``limit`` documents matching the SQL ``filter`` clause."""
        sql = "SELECT doc_id, content, is_text, metadata, created_at FROM documents"
        if filter:
            sql += f" WHERE {filter}"
        sql += " LIMIT ?"
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(sql, (limit,)) as cur,
        ):
            rows = await cur.fetchall()
        return [_row_to_document(row) for row in rows]


def _row_to_document(row: Any) -> Document:
    """Decode a ``documents`` row into a :class:`Document`."""
    doc_id, content_blob, is_text, metadata_blob, created_at = row
    content: str | bytes = (
        bytes(content_blob).decode("utf-8") if int(is_text) == 1 else bytes(content_blob)
    )
    decoded = loads_jsonb(bytes(metadata_blob))
    metadata = cast("dict[str, Any]", decoded) if isinstance(decoded, dict) else {}
    return Document(
        id=str(doc_id),
        content=content,
        metadata=metadata,
        created_at=datetime.fromisoformat(str(created_at)),
    )
