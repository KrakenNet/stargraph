# SPDX-License-Identifier: Apache-2.0
"""RyuGraphStore POC (FR-3 / FR-11, design §3.2).

Phase-1 reference :class:`harbor.stores.graph.GraphStore` implementation
backed by RyuGraph's native ``ryugraph.AsyncConnection``. RyuGraph is the
community fork of Kuzu (predictable-labs/ryugraph) after Kuzu's GitHub
repository was archived 2025-10-10 following Apple's acquisition of Kuzu
Inc. The Python API surface (Database / AsyncConnection / QueryResult)
is unchanged across the fork. POC scope:

- ``bootstrap()`` opens (or creates) a :class:`ryugraph.Database` and an
  :class:`ryugraph.AsyncConnection`, then idempotently installs the design
  §3.2 schema -- one ``Entity`` node table keyed by ``id`` plus one
  ``Rel`` edge table carrying ``predicate`` and reserved bitemporal
  ``t_valid`` / ``t_invalid`` timestamp columns.
- ``add_triple(s, p, o, props)`` runs the MERGE through
  :class:`harbor.stores.cypher.Linter` first, then upserts both
  endpoints and the edge with parameterised ``$s_id / $p / $o_id``.
- ``query(cypher, params)`` lints first, then awaits
  ``connection.execute`` and materialises the ``QueryResult`` into a
  :class:`ResultSet` of column-keyed dicts.
- ``expand(node, hops, predicates)`` validates ``0 < hops <= 10``,
  interpolates the bound as a Cypher literal (variable-length bounds
  cannot be parameterised), lints the assembled query, and returns a
  list of :class:`GraphPath`.
- ``health()`` counts entities + edges via two probe queries.

Single-writer semantics enforced through
:func:`harbor.stores._common._lock_for`. A module-level
``_RYUGRAPH_INSTANCES`` registry keyed by resolved path provides
singleton-per-path :class:`ryugraph.Database` reuse so multiple
``RyuGraphStore`` handles at the same path share one connection
(useful for in-process multi-reader access).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from harbor.errors import StoreError
from harbor.stores._common import (
    StoreHealth,
    _detect_fs_type,  # pyright: ignore[reportPrivateUsage]
    _lock_for,  # pyright: ignore[reportPrivateUsage]
    _nfs_warning,  # pyright: ignore[reportPrivateUsage]
    _validate_migration_plan,  # pyright: ignore[reportPrivateUsage]
)
from harbor.stores.cypher import Linter
from harbor.stores.graph import GraphPath, NodeRef, ResultSet

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import ryugraph

    from harbor.stores._common import MigrationPlan

__all__ = ["RyuGraphStore"]


_SCHEMA_V = 1
_MAX_HOPS = 10

_DDL_ENTITY = "CREATE NODE TABLE IF NOT EXISTS Entity(id STRING, kind STRING, PRIMARY KEY (id))"
_DDL_REL = (
    "CREATE REL TABLE IF NOT EXISTS Rel("
    "FROM Entity TO Entity, predicate STRING, "
    "t_valid TIMESTAMP DEFAULT NULL, t_invalid TIMESTAMP DEFAULT NULL)"
)

_MERGE_TRIPLE = (
    "MERGE (s:Entity {id: $s_id}) "
    "ON CREATE SET s.kind = $s_kind "
    "ON MATCH SET s.kind = $s_kind "
    "MERGE (o:Entity {id: $o_id}) "
    "ON CREATE SET o.kind = $o_kind "
    "ON MATCH SET o.kind = $o_kind "
    "MERGE (s)-[r:Rel {predicate: $p}]->(o)"
)


# Module-level singleton-per-path registry. Multiple RyuGraphStore
# instances pointed at the same on-disk path share one Database +
# AsyncConnection pair so concurrent readers in the same process don't
# fight RyuGraph's exclusive write lock at open time.
_RYUGRAPH_INSTANCES: dict[Path, RyuGraphStore] = {}


class RyuGraphStore:
    """POC :class:`GraphStore` backed by ``ryugraph.AsyncConnection`` (design §3.2)."""

    def __init__(
        self,
        path: Path,
        *,
        read_only: bool = False,
        buffer_pool_size: int = 256 * 1024 * 1024,
        max_db_size: int = 1024 * 1024 * 1024,
    ) -> None:
        self._path = path
        self._read_only = read_only
        self._version = _SCHEMA_V
        self._linter = Linter()
        self._db: ryugraph.Database | None = None
        self._conn: ryugraph.AsyncConnection | None = None
        # Cap per-Database virtual address-space request. RyuGraph's defaults
        # are buffer_pool ≈ 80% of RAM and max_db_size = 8 TB; with N
        # RyuGraphStore instances per pytest run, that exhausts process
        # VA limits as "Buffer manager exception: Mmap for size 8.8T
        # failed". 1 GB max + 256 MB buffer is plenty for POC + tests;
        # production callers can raise both via the constructor.
        self._buffer_pool_size = buffer_pool_size
        self._max_db_size = max_db_size

    # ------------------------------------------------------------------ lifecycle

    async def bootstrap(self) -> None:
        """Open the database, install the design §3.2 schema (idempotent)."""
        async with _lock_for(self._path):
            self._ensure_open()
            conn = self._require_conn()
            await conn.execute(_DDL_ENTITY)
            await conn.execute(_DDL_REL)

    async def health(self) -> StoreHealth:
        """Return :class:`StoreHealth` snapshot with node + edge counts."""
        warnings: list[str] = []
        ok = True
        node_count: int | None = None
        fragment_count: int | None = None
        try:
            self._ensure_open()
            conn = self._require_conn()
            node_count = await self._scalar_count(conn, "MATCH (e:Entity) RETURN count(e)")
            fragment_count = await self._scalar_count(conn, "MATCH ()-[r:Rel]->() RETURN count(r)")
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
            node_count=node_count,
            fragment_count=fragment_count,
            fs_type=fs_type,
            lock_state=lock_state,
            warnings=warnings,
        )

    async def migrate(self, plan: MigrationPlan) -> None:
        """POC stub -- migration support arrives in a later task.

        Narrows / renames / drops are rejected up-front by
        :func:`_validate_migration_plan` so callers see the same
        ``MigrationNotSupported`` error every store raises for v1
        unsupported ops.
        """
        _validate_migration_plan(plan, store="ryugraph")

    # ------------------------------------------------------------------ CRUD

    async def add_triple(
        self,
        s: NodeRef,
        p: str,
        o: NodeRef,
        *,
        props: Mapping[str, str] | None = None,
    ) -> None:
        """Upsert ``(s)-[p]->(o)`` via parameterised ``MERGE``."""
        self._linter.check(_MERGE_TRIPLE)
        params: dict[str, Any] = {
            "s_id": s.id,
            "s_kind": s.kind,
            "o_id": o.id,
            "o_kind": o.kind,
            "p": p,
        }
        async with _lock_for(self._path):
            self._ensure_open()
            conn = self._require_conn()
            await conn.execute(_MERGE_TRIPLE, params)

    async def query(self, cypher: str, params: Mapping[str, Any] | None = None) -> ResultSet:
        """Lint ``cypher``, execute, materialise as :class:`ResultSet`."""
        self._linter.check(cypher)
        self._ensure_open()
        conn = self._require_conn()
        result = await conn.execute(cypher, dict(params) if params else {})
        return self._result_to_resultset(result)

    # ------------------------------------------------------------------ provider extensions
    #
    # bulk_copy is intentionally OUTSIDE the GraphStore Protocol (FR-11,
    # AC-12.4): bulk-CSV ingest is RyuGraph-specific (RyuGraph's native ``COPY
    # FROM`` statement) and does not have a portable analogue across
    # every property-graph provider. Callers that opt into the RyuGraph
    # provider extension hold a ``RyuGraphStore`` reference directly;
    # ``GraphStore``-typed callers see only the portable surface.

    async def bulk_copy(self, *, entities_csv: Path, edges_csv: Path) -> None:
        """Bulk-load CSVs into ``Entity`` + ``Rel`` via RyuGraph's ``COPY FROM``.

        Provider extension (FR-11, AC-12.4): not part of the
        :class:`GraphStore` Protocol. ``entities_csv`` columns must
        match the ``Entity(id, kind)`` schema; ``edges_csv`` columns
        must match ``(from_id, to_id, predicate)``.
        """
        async with _lock_for(self._path):
            self._ensure_open()
            conn = self._require_conn()
            await conn.execute(f'COPY Entity FROM "{entities_csv}" (HEADER=true)')
            await conn.execute(f'COPY Rel FROM "{edges_csv}" (HEADER=true)')

    async def expand(
        self,
        node: NodeRef,
        hops: int = 1,
        *,
        predicates: Sequence[str] | None = None,
    ) -> list[GraphPath]:
        """Return walks from ``node`` up to ``hops`` deep (design §3.2)."""
        if not (0 < hops <= _MAX_HOPS):
            msg = f"hops must satisfy 0 < hops <= {_MAX_HOPS}, got {hops}"
            raise ValueError(msg)

        # Variable-length bounds cannot be parameterised; the bound is
        # an int we just validated, so interpolating the literal is safe.
        cypher = (
            f"MATCH p=(s:Entity {{id: $start}})-[r:Rel*1..{hops}]-(o:Entity) "
            "RETURN nodes(p) AS ns, rels(p) AS es"
        )
        self._linter.check(cypher)

        self._ensure_open()
        conn = self._require_conn()
        result = await conn.execute(cypher, {"start": node.id})

        allowed: set[str] | None = set(predicates) if predicates else None
        paths: list[GraphPath] = []
        for raw_row in self._iter_rows(result):
            row = cast("list[Any]", raw_row)
            ns = cast("list[dict[str, Any]]", row[0] if len(row) > 0 else [])
            es = cast("list[dict[str, Any]]", row[1] if len(row) > 1 else [])

            edges: list[dict[str, Any]] = []
            ok = True
            for edge in es:
                pred_obj = edge.get("predicate")
                pred = pred_obj if isinstance(pred_obj, str) else ""
                if allowed is not None and pred not in allowed:
                    ok = False
                    break
                edges.append({"predicate": pred})
            if not ok:
                continue

            nodes: list[NodeRef] = []
            for n in ns:
                nid_obj = n.get("id")
                nkind_obj = n.get("kind")
                nid = nid_obj if isinstance(nid_obj, str) else ""
                nkind = nkind_obj if isinstance(nkind_obj, str) else ""
                nodes.append(NodeRef(id=nid, kind=nkind))

            paths.append(GraphPath(nodes=nodes, edges=edges))
        return paths

    # ------------------------------------------------------------------ helpers

    def _ensure_open(self) -> None:
        """Lazily open Database + AsyncConnection, sharing per resolved path."""
        if self._conn is not None:
            return
        key = self._path.resolve() if self._path.exists() else self._path
        cached = _RYUGRAPH_INSTANCES.get(key)
        if cached is not None and cached._conn is not None:
            self._db = cached._db
            self._conn = cached._conn
            return
        # ryugraph is a stores-extra dependency; import lazily so engine /
        # serve subsystems that re-export RyuGraphStore through
        # ``harbor.stores`` (for type-only purposes) can load the module
        # without forcing the stores-extra wheel install on every CI job.
        import ryugraph

        db = ryugraph.Database(
            str(self._path),
            read_only=self._read_only,
            buffer_pool_size=self._buffer_pool_size,
            max_db_size=self._max_db_size,
        )
        conn = ryugraph.AsyncConnection(db)
        self._db = db
        self._conn = conn
        _RYUGRAPH_INSTANCES[key] = self

    def _require_conn(self) -> ryugraph.AsyncConnection:
        if self._conn is None:
            msg = "RyuGraphStore connection not open; call bootstrap() first"
            raise StoreError(msg)
        return self._conn

    @staticmethod
    def _first_result(
        result: ryugraph.QueryResult | list[ryugraph.QueryResult],
    ) -> ryugraph.QueryResult | None:
        if isinstance(result, list):
            return result[0] if result else None
        return result

    @classmethod
    def _iter_rows(cls, result: ryugraph.QueryResult | list[ryugraph.QueryResult]) -> list[Any]:
        target = cls._first_result(result)
        if target is None:
            return []
        return list(target)

    @classmethod
    def _result_to_resultset(
        cls, result: ryugraph.QueryResult | list[ryugraph.QueryResult]
    ) -> ResultSet:
        target = cls._first_result(result)
        if target is None:
            return ResultSet()
        cols_obj = target.get_column_names()
        columns = [c for c in cast("list[Any]", cols_obj) if isinstance(c, str)]
        rows: list[dict[str, Any]] = []
        for raw in target:
            values = cast("list[Any]", raw)
            rows.append({columns[i]: values[i] for i in range(len(columns))})
        return ResultSet(rows=rows, columns=columns)

    @classmethod
    async def _scalar_count(cls, conn: ryugraph.AsyncConnection, cypher: str) -> int | None:
        result = await conn.execute(cypher)
        target = cls._first_result(result)
        if target is None:
            return None
        for raw in target:
            values = cast("list[Any]", raw)
            if values:
                head = values[0]
                if isinstance(head, int):
                    return head
        return None
