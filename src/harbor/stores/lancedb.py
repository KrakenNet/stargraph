# SPDX-License-Identifier: Apache-2.0
"""LanceDBVectorStore POC (FR-2 / FR-8 / FR-10 / FR-16, design §3.1).

Phase-1 reference :class:`harbor.stores.vector.VectorStore`
implementation backed by LanceDB. POC scope:

- ``bootstrap()`` opens (or creates) the table and writes the FR-8
  5-tuple drift gate ``(model_id, revision, content_hash, ndims,
  schema_v)`` into a sidecar ``_harbor_meta`` table. AsyncTable has no
  ``replace_schema_metadata`` analog yet, so the sidecar pattern is the
  simplest portable choice.
- ``upsert(rows)`` embeds any rows missing a vector (calling
  ``embedder.embed(kind="document")``) and ``merge_insert`` on ``id``.
- ``search(...)`` supports ``mode="vector"``, ``"fts"``, ``"hybrid"``;
  hybrid runs both per-store branches and fuses via :class:`RRFReranker`.
- ``delete(ids)`` issues a ``WHERE id IN (...)`` table delete.
- ``health()`` reports ``fragment_count`` from ``table.stats()``.

All write paths serialise through :func:`harbor.stores._common._lock_for`
to enforce single-writer-per-path (FR-9).
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import lancedb.index  # pyright: ignore[reportMissingTypeStubs]
import pyarrow as pa

from harbor.errors import StoreError
from harbor.stores._common import (
    _EMBED_META_TABLE,  # pyright: ignore[reportPrivateUsage]
    StoreHealth,
    _detect_fs_type,  # pyright: ignore[reportPrivateUsage]
    _lock_for,  # pyright: ignore[reportPrivateUsage]
    _nfs_warning,  # pyright: ignore[reportPrivateUsage]
    _validate_migration_plan,  # pyright: ignore[reportPrivateUsage]
    _verify_embed_metadata,  # pyright: ignore[reportPrivateUsage]
    _write_embed_metadata,  # pyright: ignore[reportPrivateUsage]
)
from harbor.stores.rerankers import RRFReranker
from harbor.stores.vector import Hit

from pathlib import Path

if TYPE_CHECKING:
    from harbor.stores._common import MigrationPlan
    from harbor.stores.embeddings import Embedding
    from harbor.stores.vector import Row

__all__ = ["LanceDBVectorStore"]


_SCHEMA_V = 1


class LanceDBVectorStore:
    """POC :class:`VectorStore` backed by LanceDB (design §3.1)."""

    def __init__(
        self,
        path: Path,
        embedder: Embedding,
        *,
        table_name: str = "vectors",
        tmp_dir: Path | None = None,
    ) -> None:
        self._path = path
        self._embedder = embedder
        self._table_name = table_name
        self._version = _SCHEMA_V
        # FR-9 / lance#2461: keep FTS scratch off the table dir to avoid
        # cross-process write contention. Default ``<path>/.tmp``.
        self._tmp_dir = tmp_dir if tmp_dir is not None else path / ".tmp"

    # ------------------------------------------------------------------ lifecycle

    async def bootstrap(self) -> None:
        """Create the vector table + meta sidecar; verify drift gate on re-entry."""
        self._apply_tmp_dir()
        async with _lock_for(self._path):
            db = await lancedb.connect_async(self._path)
            existing = set(await self._list_tables(db))

            if _EMBED_META_TABLE in existing:
                await _verify_embed_metadata(
                    db,
                    self._embedder,
                    _SCHEMA_V,
                    store="lancedb",
                    path=self._path,
                    table=self._table_name,
                )
            else:
                await _write_embed_metadata(db, self._embedder, _SCHEMA_V)

            if self._table_name not in existing:
                schema = self._table_schema(self._embedder.ndims)
                await db.create_table(self._table_name, schema=schema)  # pyright: ignore[reportUnknownMemberType]

    async def health(self) -> StoreHealth:
        """Return :class:`StoreHealth` snapshot with ``fragment_count``."""
        fragment_count: int | None = None
        warnings: list[str] = []
        ok = True
        try:
            db = await lancedb.connect_async(self._path)
            existing = set(await self._list_tables(db))
            if self._table_name in existing:
                tbl = await db.open_table(self._table_name)
                stats = await tbl.stats()
                fragment_count = self._fragment_count_from_stats(stats)
            else:
                warnings.append("table not bootstrapped")
                ok = False
        except (OSError, RuntimeError) as exc:  # pragma: no cover - defensive
            warnings.append(f"health probe failed: {exc!r}")
            ok = False

        lock = _lock_for(self._path)
        lock_state: Literal["free", "held"] = "held" if lock.locked() else "free"
        fs_type = _detect_fs_type(self._path)
        nfs_warning = _nfs_warning(fs_type)
        if nfs_warning is not None:
            warnings.append(nfs_warning)
        return StoreHealth(
            ok=ok,
            version=self._version,
            fragment_count=fragment_count,
            embedding_hash=self._embedder.content_hash,
            fs_type=fs_type,
            lock_state=lock_state,
            warnings=warnings,
        )

    async def migrate(self, plan: MigrationPlan) -> None:
        """Validate-then-noop migration (T04).

        Narrows / renames / drops are rejected up-front by
        :func:`_validate_migration_plan` so callers see
        ``MigrationNotSupported`` for unsupported ops.
        Valid ``add_column nullable=True`` plans return silently.
        """
        _validate_migration_plan(plan, store="lancedb")

    async def current_version(self) -> int:
        """Return the current LanceDB table version (FR-10).

        Engine checkpoints record this value alongside ``run_id`` / ``step``
        so a future :meth:`AsyncTable.checkout` reproduces the exact dataset
        snapshot that produced the checkpoint -- the natural reproducibility
        hook called out in design §3.1.
        """
        db = await lancedb.connect_async(self._path)
        tbl = await db.open_table(self._table_name)
        return int(await tbl.version())

    async def cleanup_old_versions(self, older_than_days: int = 7) -> None:
        """Prune dataset versions older than ``older_than_days`` (FR-10).

        Wraps ``AsyncTable.optimize(cleanup_older_than=...)``, which acts
        as LanceDB's ``VACUUM`` analog (compact + prune). Held under the
        single-writer lock; Phase 3 will schedule this from the
        consolidation-cadence rule.
        """
        async with _lock_for(self._path):
            db = await lancedb.connect_async(self._path)
            existing = set(await self._list_tables(db))
            if self._table_name not in existing:
                return
            tbl = await db.open_table(self._table_name)
            await tbl.optimize(cleanup_older_than=timedelta(days=older_than_days))

    # ------------------------------------------------------------------ CRUD

    async def upsert(self, rows: list[Row]) -> None:
        """Embed missing vectors and ``merge_insert`` on ``id``."""
        if not rows:
            return

        ndims = self._embedder.ndims
        missing_idx = [i for i, r in enumerate(rows) if r.vector is None]
        missing_texts = [rows[i].text or "" for i in missing_idx]
        if missing_idx:
            embedded = await self._embedder.embed(missing_texts, kind="document")
            if len(embedded) != len(missing_idx):
                msg = "embedder returned wrong number of vectors"
                raise StoreError(msg)
        else:
            embedded = []

        ids: list[str] = []
        vectors: list[list[float]] = []
        texts: list[str] = []
        metadata: list[str] = []
        emb_iter = iter(embedded)
        for row in rows:
            ids.append(row.id)
            vec = next(emb_iter) if row.vector is None else row.vector
            if len(vec) != ndims:
                msg = f"vector for id={row.id!r} has length {len(vec)} but embedder.ndims={ndims}"
                raise ValueError(msg)
            vectors.append(vec)
            texts.append(row.text or "")
            metadata.append(json.dumps(dict(row.metadata), sort_keys=True))

        arrow_table = pa.table(
            {
                "id": pa.array(ids, type=pa.string()),
                "vector": pa.array(vectors, type=pa.list_(pa.float32(), ndims)),
                "text": pa.array(texts, type=pa.string()),
                "metadata": pa.array(metadata, type=pa.string()),
            },
            schema=self._table_schema(ndims),
        )

        async with _lock_for(self._path):
            db = await lancedb.connect_async(self._path)
            tbl = await db.open_table(self._table_name)
            await (
                tbl.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(arrow_table)  # pyright: ignore[reportUnknownMemberType]
            )

    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,  # noqa: A002
        k: int = 10,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]:
        """Top-``k`` :class:`Hit` rows for the requested ``mode``.

        For ergonomics, if ``mode='vector'`` (the default) but only
        ``text`` is supplied, fall back to ``mode='fts'``. Explicit
        callers passing ``mode='vector'`` with a ``vector`` argument get
        the strict pure-ANN behaviour.
        """
        if mode == "vector" and vector is None and text is not None:
            mode = "fts"
        if mode == "vector":
            if vector is None:
                msg = "mode='vector' requires a vector argument"
                raise ValueError(msg)
            return await self._vector_search(vector, filter=filter, k=k)
        if mode == "fts":
            if text is None:
                msg = "mode='fts' requires a text argument"
                raise ValueError(msg)
            return await self._fts_search(text, filter=filter, k=k)
        # hybrid -- requires at least one input branch
        branches: list[list[Hit]] = []
        if vector is not None:
            branches.append(await self._vector_search(vector, filter=filter, k=k))
        if text is not None:
            branches.append(await self._fts_search(text, filter=filter, k=k))
        if not branches:
            msg = "mode='hybrid' requires at least one of vector or text"
            raise ValueError(msg)
        reranker = RRFReranker()
        return await reranker.fuse(branches, k=k)

    async def delete(self, ids: list[str]) -> int:
        """Delete rows by ``id``; returns number of rows deleted."""
        if not ids:
            return 0
        async with _lock_for(self._path):
            db = await lancedb.connect_async(self._path)
            tbl = await db.open_table(self._table_name)
            before = await tbl.count_rows()
            quoted = ", ".join(self._quote_id(i) for i in ids)
            await tbl.delete(f"id IN ({quoted})")
            after = await tbl.count_rows()
        return max(0, before - after)

    # ------------------------------------------------------------------ helpers

    def _apply_tmp_dir(self) -> None:
        """Point the lance scratch dir at ``self._tmp_dir`` (lance#2461).

        ``lance``'s FTS writer streams temp segments through
        ``LANCE_TEMP_DIR``; defaulting to a per-store ``<path>/.tmp``
        avoids cross-store/cross-test contention without requiring callers
        to manage globals. Idempotent and a best-effort; honoured only if
        the env var has not already been set explicitly.
        """
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("LANCE_TEMP_DIR", str(self._tmp_dir))

    @staticmethod
    def _table_schema(ndims: int) -> pa.Schema:
        return pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), ndims)),
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()),
            ],
        )

    @staticmethod
    async def _list_tables(db: Any) -> list[str]:
        """Extract a flat ``list[str]`` of table names from ``list_tables()``.

        LanceDB returns a ``ListTablesResponse`` Pydantic model; we want
        just the names. Boxing here keeps the rest of the file from
        carrying ``Any``.
        """
        resp = await db.list_tables()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        tables: object = getattr(resp, "tables", resp)
        if isinstance(tables, list):
            return [t for t in cast("list[object]", tables) if isinstance(t, str)]
        return []

    @staticmethod
    def _fragment_count_from_stats(stats: object) -> int | None:
        if not isinstance(stats, dict):
            return None
        stats_dict = cast("dict[str, object]", stats)
        frag = stats_dict.get("fragment_stats")
        if isinstance(frag, dict):
            value = cast("dict[str, object]", frag).get("num_fragments")
            if isinstance(value, int):
                return value
        return None

    @staticmethod
    def _quote_id(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    async def _vector_search(
        self,
        vector: list[float],
        *,
        filter: str | None,  # noqa: A002
        k: int,
    ) -> list[Hit]:
        db = await lancedb.connect_async(self._path)
        tbl = await db.open_table(self._table_name)
        query = tbl.vector_search(vector).limit(k)  # pyright: ignore[reportUnknownMemberType]
        if filter is not None:
            query = query.where(filter)
        rows = (await query.to_arrow()).to_pylist()
        return [self._row_to_hit(r, score_key="_distance", invert=True) for r in rows]

    async def _fts_search(
        self,
        text: str,
        *,
        filter: str | None,  # noqa: A002
        k: int,
    ) -> list[Hit]:
        db = await lancedb.connect_async(self._path)
        tbl = await db.open_table(self._table_name)
        # Ensure FTS index exists; create_index is idempotent at the storage layer
        # but the Async API raises on re-create -- swallow that here.
        with contextlib.suppress(RuntimeError, OSError, ValueError):
            await tbl.create_index("text", config=lancedb.index.FTS())
        query = tbl.query().nearest_to_text(text).limit(k)
        if filter is not None:
            query = query.where(filter)
        rows = (await query.to_arrow()).to_pylist()
        return [self._row_to_hit(r, score_key="_score", invert=False) for r in rows]

    @staticmethod
    def _row_to_hit(row: dict[str, object], *, score_key: str, invert: bool) -> Hit:
        raw_id = row.get("id")
        hit_id = raw_id if isinstance(raw_id, str) else ""
        raw_score = row.get(score_key, 0.0)
        score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
        if invert:
            score = -score  # smaller distance == better
        raw_meta = row.get("metadata")
        metadata: dict[str, str | int | float | bool] = {}
        if isinstance(raw_meta, str) and raw_meta:
            try:
                parsed = json.loads(raw_meta)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                for k, v in cast("dict[str, object]", parsed).items():
                    if isinstance(v, (str, int, float, bool)):
                        metadata[k] = v
        return Hit(id=hit_id, score=score, metadata=metadata)
