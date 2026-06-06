# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed :class:`FactStore` provider (FR-6, FR-13, FR-30, design §3.5).

POC scope (Phase 1, tasks 1.18 + 1.29): ``bootstrap`` creates the ``facts``
table; ``pin`` / ``unpin`` / ``query`` round-trip :class:`Fact` rows with
orjson-JSONB payload + lineage; ``apply_delta`` switches on the discriminated
:data:`stargraph.stores.memory.MemoryDelta` ``kind`` field, treating UPDATE/DELETE
as ``unpin(replaces)`` followed by ``pin(new)`` for ADD/UPDATE. Provenance
fields (``rule_id``, ``source_episode_ids``, ``promotion_ts``) are validated
non-empty before any store mutation -- the lineage row written to ``facts``
is the only acceptable promotion path from
:class:`stargraph.stores.memory.MemoryStore` (design §4.2). Embedding-based
similarity dedup over the ``add`` path is **deferred to Phase 3** -- the POC
trusts the consolidation rule output verbatim. Single-writer serialization
via :func:`stargraph.stores._common._lock_for` (FR-9).

POC ``query`` is a basic equality match on ``user`` / ``agent`` columns plus
optional payload-field equality on ``subject`` / ``predicate`` / ``object``;
full pattern matching (regex, triple-slot wildcards) lands in Phase 3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import aiosqlite

from stargraph.stores._common import (
    MigrationPlan,
    StoreHealth,
    _detect_fs_type,  # pyright: ignore[reportPrivateUsage]
    _lock_for,  # pyright: ignore[reportPrivateUsage]
    _nfs_warning,  # pyright: ignore[reportPrivateUsage]
    _validate_migration_plan,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.stores._delta import _resolve_replaces, _validate_delta_provenance
from stargraph.stores._sqlite_base import (
    _apply_pragmas,
    _ensure_migrations_table,
    dumps_jsonb,
    loads_jsonb,
)
from stargraph.stores.fact import Fact, FactPattern
from stargraph.stores.memory import DeleteDelta, NoopDelta, UpdateDelta

if TYPE_CHECKING:
    from stargraph.stores.memory import MemoryDelta

__all__ = ["SQLiteFactStore"]


_BOOTSTRAP_DDL = (
    "CREATE TABLE IF NOT EXISTS facts ("
    "  fact_id    TEXT PRIMARY KEY,"
    "  user       TEXT NOT NULL,"
    "  agent      TEXT NOT NULL,"
    "  payload    BLOB NOT NULL,"
    "  lineage    BLOB NOT NULL,"
    "  confidence REAL NOT NULL,"
    "  metadata   BLOB NOT NULL,"
    "  pinned_at  TEXT NOT NULL"
    ")"
)


class SQLiteFactStore:
    """SQLite ``FactStore`` (design §3.5) -- POC pin/unpin/query/apply_delta.

    ``payload`` and ``lineage`` round-trip through :func:`dumps_jsonb` /
    :func:`loads_jsonb`. ``apply_delta`` is the only acceptable promotion
    path from :class:`stargraph.stores.memory.MemoryStore` typed deltas (design
    §4.2 lineage); UPDATE/DELETE unpin every id in ``replaces`` before the
    new ADD/UPDATE row lands.
    """

    def __init__(self, path: Path) -> None:
        """Create a fact store rooted at ``path`` (file is created on bootstrap)."""
        self._path = path

    async def bootstrap(self) -> None:
        """Idempotent schema bootstrap (creates ``facts`` table)."""
        async with _lock_for(self._path):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._path) as conn:
                await _apply_pragmas(conn)
                await _ensure_migrations_table(conn)
                await conn.execute(_BOOTSTRAP_DDL)
                await conn.commit()

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (POC: minimal fields)."""
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute("SELECT COUNT(*) FROM facts") as cur,
        ):
            row = await cur.fetchone()
        count = int(row[0]) if row is not None else 0
        fs_type = _detect_fs_type(self._path)
        warnings: list[str] = []
        nfs_warning = _nfs_warning(fs_type)
        if nfs_warning is not None:
            warnings.append(nfs_warning)
        return StoreHealth(
            ok=True,
            version=1,
            fragment_count=count,
            fs_type=fs_type,
            lock_state="free",
            warnings=warnings,
        )

    async def migrate(self, plan: MigrationPlan) -> None:
        """POC stub -- migration replay lands in Phase 3.

        Narrows / renames / drops are rejected up-front by
        :func:`_validate_migration_plan` so callers see the same
        ``MigrationNotSupported`` error every store raises for v1
        unsupported ops.
        """
        _validate_migration_plan(plan, store="sqlite_fact")

    async def pin(self, fact: Fact) -> None:
        """Persist ``fact`` (insert-or-replace by ``fact.id``)."""
        payload_blob = dumps_jsonb(fact.payload)
        lineage_blob = dumps_jsonb(fact.lineage)
        metadata_blob = dumps_jsonb(fact.metadata)
        async with _lock_for(self._path), aiosqlite.connect(self._path) as conn:
            await _apply_pragmas(conn)
            await conn.execute(
                "INSERT OR REPLACE INTO facts"
                " (fact_id, user, agent, payload, lineage, confidence,"
                "  metadata, pinned_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fact.id,
                    fact.user,
                    fact.agent,
                    payload_blob,
                    lineage_blob,
                    fact.confidence,
                    metadata_blob,
                    fact.pinned_at.isoformat(),
                ),
            )
            await conn.commit()

    async def unpin(self, fact_id: str) -> None:
        """Remove the fact identified by ``fact_id`` (no-op if absent)."""
        async with _lock_for(self._path), aiosqlite.connect(self._path) as conn:
            await _apply_pragmas(conn)
            await conn.execute(
                "DELETE FROM facts WHERE fact_id = ?",
                (fact_id,),
            )
            await conn.commit()

    async def query(self, pattern: FactPattern) -> list[Fact]:
        """Return every :class:`Fact` matching ``pattern`` (POC: equality match).

        Filters on ``user`` / ``agent`` columns; ``subject`` / ``predicate`` /
        ``object`` slots are matched against the equally-named keys in the
        decoded ``payload`` dict (skipped when absent). Full pattern semantics
        (regex, wildcard composition) land in Phase 3.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if pattern.user is not None:
            clauses.append("user = ?")
            params.append(pattern.user)
        if pattern.agent is not None:
            clauses.append("agent = ?")
            params.append(pattern.agent)
        sql = (
            "SELECT fact_id, user, agent, payload, lineage, confidence,"
            " metadata, pinned_at FROM facts"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(sql, params) as cur,
        ):
            rows = await cur.fetchall()

        facts = [_row_to_fact(row) for row in rows]
        return [f for f in facts if _payload_matches(f.payload, pattern)]

    async def apply_delta(self, delta: MemoryDelta) -> None:
        """Apply a Mem0-style typed :data:`MemoryDelta` (design §4.2, FR-29).

        POC switch on ``kind``:

        * ``add`` -- pin a new fact built from ``fact_payload`` + provenance.
        * ``update`` -- unpin every id in ``replaces``, then pin the new fact.
        * ``delete`` -- unpin every id in ``replaces``.
        * ``noop`` -- audit-only; no store mutation.

        Provenance fields (``rule_id``, ``source_episode_ids``,
        ``promotion_ts``, ``confidence``) -- plus ``replaces`` for UPDATE /
        DELETE -- are validated via :func:`_validate_delta_provenance`
        before any mutation runs so a malformed delta cannot silently
        land in the lineage column. UPDATE / DELETE additionally call
        :func:`_resolve_replaces` to surface the matching :class:`Fact`
        rows (lineage chaining lands in Phase 3; today the resolved list
        is consumed only by the unpin loop). Embedding-similarity dedup
        over the ``add`` path is deferred to Phase 3 -- the POC trusts
        the consolidation rule output verbatim.
        """
        _validate_delta_provenance(delta)
        if isinstance(delta, NoopDelta):
            return
        if isinstance(delta, UpdateDelta | DeleteDelta):
            await _resolve_replaces(self, delta.replaces)
            for fact_id in delta.replaces:
                await self.unpin(fact_id)
            if isinstance(delta, DeleteDelta):
                return
        await self.pin(_build_promoted_fact(delta.fact_payload, delta))


def _build_promoted_fact(
    payload: dict[str, Any],
    delta: Any,
) -> Fact:
    """Construct a :class:`Fact` from an ADD / UPDATE delta payload + provenance."""
    fact_id_raw = payload.get("id")
    fact_id = str(fact_id_raw) if fact_id_raw is not None else str(uuid4())
    user_raw = payload.get("user", "")
    agent_raw = payload.get("agent", "")
    return Fact(
        id=fact_id,
        user=str(user_raw),
        agent=str(agent_raw),
        payload=payload,
        lineage=[
            {
                "rule_id": delta.rule_id,
                "source_episode_ids": delta.source_episode_ids,
                "promotion_ts": delta.promotion_ts.isoformat(),
            }
        ],
        confidence=delta.confidence,
        pinned_at=datetime.now(UTC),
    )


def _row_to_fact(row: Any) -> Fact:
    """Decode a ``facts`` row into a :class:`Fact`."""
    fact_id, user, agent, payload_blob, lineage_blob, confidence, metadata_blob, pinned_at = row
    payload_decoded = loads_jsonb(bytes(payload_blob))
    lineage_decoded = loads_jsonb(bytes(lineage_blob))
    metadata_decoded = loads_jsonb(bytes(metadata_blob))
    payload = cast("dict[str, Any]", payload_decoded) if isinstance(payload_decoded, dict) else {}
    lineage = (
        cast("list[dict[str, Any]]", lineage_decoded) if isinstance(lineage_decoded, list) else []
    )
    metadata = (
        cast("dict[str, Any]", metadata_decoded) if isinstance(metadata_decoded, dict) else {}
    )
    return Fact(
        id=str(fact_id),
        user=str(user),
        agent=str(agent),
        payload=payload,
        lineage=lineage,
        confidence=float(confidence),
        pinned_at=datetime.fromisoformat(str(pinned_at)),
        metadata=metadata,
    )


def _payload_matches(payload: dict[str, Any], pattern: FactPattern) -> bool:
    """POC equality match on ``subject`` / ``predicate`` / ``object`` payload keys."""
    if pattern.subject is not None and payload.get("subject") != pattern.subject:
        return False
    if pattern.predicate is not None and payload.get("predicate") != pattern.predicate:
        return False
    return not (pattern.object is not None and payload.get("object") != pattern.object)
