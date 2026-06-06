# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed :class:`MemoryStore` POC (FR-5, FR-13, FR-27, FR-28, design §3.4).

Episodic memory rows keyed by a ``(user, session, agent)`` 3-tuple,
encoded as a trailing-separator scope key
(``/user/{user}/session/{session}/agent/{agent}/``). The trailing slash
prevents prefix-collision on widening ``LIKE`` reads -- without it,
``/user/alice/...`` would match ``/user/aliceX/...`` via ``LIKE
'/user/alice%'`` (FR-27).

POC scope (full implementation lands in Phase 3):

* ``bootstrap`` -- creates the ``episodes`` table and applies WAL pragmas.
* ``put`` -- inserts an episode under the encoded scope key.
* ``recent`` -- widening read; omitted scope levels become ``%`` LIKE
  wildcards, with the trailing separator preserved on the right.
* ``consolidate`` -- POC: read episodes matching ``rule.when_filter`` and
  emit one :class:`stargraph.stores.memory.AddDelta` per match. Embedding-
  based similarity dedup (Mem0-style) lands in Phase 3.

Single-writer serialization via :func:`stargraph.stores._common._lock_for`
keyed on the resolved DB path (FR-9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import Any, cast

import aiosqlite

from stargraph.stores._common import (
    MigrationPlan,
    StoreHealth,
    _detect_fs_type,  # pyright: ignore[reportPrivateUsage]
    _lock_for,  # pyright: ignore[reportPrivateUsage]
    _nfs_warning,  # pyright: ignore[reportPrivateUsage]
    _validate_migration_plan,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.stores._delta import _validate_delta_provenance
from stargraph.stores._sqlite_base import (
    _apply_pragmas,
    dumps_jsonb,
    loads_jsonb,
)
from stargraph.stores.memory import (
    AddDelta,
    ConsolidationRule,
    DeleteDelta,
    Episode,
    MemoryDelta,
    NoopDelta,
    UpdateDelta,
)

__all__ = ["SQLiteMemoryStore"]


def _encode_scope_key(user: str, session: str, agent: str) -> str:
    """Encode a ``(user, session, agent)`` 3-tuple as a trailing-separator key."""
    return f"/user/{user}/session/{session}/agent/{agent}/"


def _widening_pattern(
    user: str,
    session: str | None,
    agent: str | None,
) -> str:
    """Build the LIKE pattern for a widening read (FR-27).

    Omitted scope levels become ``%`` wildcards; the trailing separator
    is always preserved so prefix collisions across user/session/agent
    boundaries are impossible.
    """
    s = session if session is not None else "%"
    a = agent if agent is not None else "%"
    return f"/user/{user}/session/{s}/agent/{a}/"


class SQLiteMemoryStore:
    """SQLite-backed episodic :class:`stargraph.stores.memory.MemoryStore` (POC).

    Rows live in a single ``episodes`` table; the
    ``(user, session, agent)`` 3-tuple is encoded as a
    trailing-separator scope key column for widening LIKE reads.
    """

    def __init__(self, path: Path) -> None:
        """Create a memory store rooted at ``path`` (file is created on bootstrap)."""
        self._path = path

    async def bootstrap(self) -> None:
        """Idempotent schema bootstrap: applies WAL pragmas and creates ``episodes``."""
        async with _lock_for(self._path):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._path) as conn:
                await _apply_pragmas(conn)
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS episodes ("
                    "  id         TEXT PRIMARY KEY,"
                    "  scope_key  TEXT NOT NULL,"
                    "  content    BLOB NOT NULL,"
                    "  timestamp  TEXT NOT NULL,"
                    "  metadata   BLOB NOT NULL"
                    ")"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodes_scope_ts "
                    "ON episodes(scope_key, timestamp DESC)"
                )
                await conn.commit()

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (POC: minimal fields)."""
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute("SELECT COUNT(*) FROM episodes") as cur,
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
        _validate_migration_plan(plan, store="sqlite_memory")

    async def put(
        self,
        episode: Episode,
        *,
        user: str,
        session: str,
        agent: str,
    ) -> None:
        """Insert ``episode`` under the encoded ``(user, session, agent)`` scope."""
        scope_key = _encode_scope_key(user, session, agent)
        payload: dict[str, Any] = {
            "content": episode.content,
            "source_node": episode.source_node,
            "agent": episode.agent,
            "user": episode.user,
            "session": episode.session,
        }
        async with _lock_for(self._path), aiosqlite.connect(self._path) as conn:
            await _apply_pragmas(conn)
            await conn.execute(
                "INSERT OR REPLACE INTO episodes "
                "(id, scope_key, content, timestamp, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    episode.id,
                    scope_key,
                    dumps_jsonb(payload),
                    episode.timestamp.isoformat(),
                    dumps_jsonb(episode.metadata),
                ),
            )
            await conn.commit()

    async def recent(
        self,
        user: str,
        session: str | None = None,
        agent: str | None = None,
        *,
        limit: int,
    ) -> list[Episode]:
        """Return the most-recent ``limit`` episodes matching the widening scope."""
        pattern = _widening_pattern(user, session, agent)
        async with aiosqlite.connect(self._path) as conn:
            await _apply_pragmas(conn)
            async with conn.execute(
                "SELECT id, content, timestamp, metadata FROM episodes "
                "WHERE scope_key LIKE ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (pattern, limit),
            ) as cur:
                rows = await cur.fetchall()

        return [_row_to_episode(row) for row in rows]

    async def consolidate(self, rule: ConsolidationRule) -> list[MemoryDelta]:
        """Run ``rule`` against stored episodes; emit Mem0-style typed deltas.

        Selects every episode matching ``rule.when_filter`` (optional SQL
        WHERE-clause fragment over the ``episodes`` table; empty string ==
        match-all) and classifies each into one of the four
        :data:`MemoryDelta` variants per the design §3.4 / FR-28 contract:

        * ``ADD`` -- episode contributes a new ``(subject, predicate)``
          fact (the default classification).
        * ``UPDATE`` -- episode supersedes one or more existing facts;
          either via explicit ``metadata["intent"] == "update"`` +
          ``metadata["replaces"]`` (caller-driven, e.g. when the upstream
          pipeline already knows the prior fact id) or via intra-batch
          exact-match dedup on ``(subject, predicate)`` (newer episode
          replaces the older episode's would-be ADD).
        * ``DELETE`` -- episode retracts one or more existing facts via
          explicit ``metadata["intent"] == "delete"`` +
          ``metadata["replaces"]``.
        * ``NOOP`` -- episode is fully redundant; audit-only via explicit
          ``metadata["intent"] == "noop"``.

        Cross-store dedup against existing :class:`stargraph.stores.fact.Fact`
        rows is **not** performed inside :meth:`consolidate` -- the
        :class:`MemoryStore` Protocol takes only ``rule`` (no FactStore
        handle) and broadening the Protocol is out of scope for the POC.
        Callers that need pre-existing-fact classification encode the
        intent on the episode (Mem0 pattern: classification happens
        upstream of episodic write); embedding-similarity dedup lands in
        a later phase.
        """
        sql = "SELECT id, content, timestamp, metadata FROM episodes ORDER BY timestamp ASC"
        where = rule.when_filter.strip()
        if where:
            sql = (
                "SELECT id, content, timestamp, metadata FROM episodes"
                f" WHERE {where} ORDER BY timestamp ASC"
            )
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(sql) as cur,
        ):
            rows = await cur.fetchall()

        episodes = [_row_to_episode(row) for row in rows]
        promotion_ts = datetime.now(UTC)
        deltas: list[MemoryDelta] = []
        # Track the most-recent ADD-emitting episode id per (subject, predicate)
        # within this consolidation pass so an exact-match repeat fires UPDATE.
        seen_keys: dict[tuple[str, str], str] = {}
        for episode in episodes:
            delta = _classify_episode(episode, rule, promotion_ts, seen_keys)
            _validate_delta_provenance(delta)
            deltas.append(delta)
        return deltas


def _classify_episode(
    episode: Episode,
    rule: ConsolidationRule,
    promotion_ts: datetime,
    seen_keys: dict[tuple[str, str], str],
) -> MemoryDelta:
    """Map ``episode`` to its Mem0-style typed :data:`MemoryDelta`.

    Honours an explicit ``metadata["intent"]`` selector
    (``"add"`` / ``"update"`` / ``"delete"`` / ``"noop"``) when present;
    otherwise defaults to ``ADD`` with intra-batch exact-match dedup on
    ``(subject, predicate)``. Mutates ``seen_keys`` to track the most-recent
    episode id per key so the next-seen duplicate emits ``UPDATE`` against it.
    """
    intent = episode.metadata.get("intent")
    fact_payload = _episode_fact_payload(episode)
    source_ids = [episode.id]
    if intent == "noop":
        return NoopDelta(
            kind="noop",
            source_episode_ids=source_ids,
            promotion_ts=promotion_ts,
            rule_id=rule.id,
            confidence=1.0,
        )
    if intent == "delete":
        return DeleteDelta(
            kind="delete",
            replaces=_coerce_replaces(episode.metadata.get("replaces")),
            source_episode_ids=source_ids,
            promotion_ts=promotion_ts,
            rule_id=rule.id,
            confidence=1.0,
        )
    if intent == "update":
        return UpdateDelta(
            kind="update",
            replaces=_coerce_replaces(episode.metadata.get("replaces")),
            fact_payload=fact_payload,
            source_episode_ids=source_ids,
            promotion_ts=promotion_ts,
            rule_id=rule.id,
            confidence=1.0,
        )
    # Default: ADD, with intra-batch exact-match dedup → UPDATE on repeat.
    subject = str(episode.metadata.get("subject", ""))
    predicate = str(episode.metadata.get("predicate", ""))
    if subject and predicate:
        key = (subject, predicate)
        prior = seen_keys.get(key)
        seen_keys[key] = episode.id
        if prior is not None:
            return UpdateDelta(
                kind="update",
                replaces=[prior],
                fact_payload=fact_payload,
                source_episode_ids=source_ids,
                promotion_ts=promotion_ts,
                rule_id=rule.id,
                confidence=1.0,
            )
    return AddDelta(
        kind="add",
        fact_payload=fact_payload,
        source_episode_ids=source_ids,
        promotion_ts=promotion_ts,
        rule_id=rule.id,
        confidence=1.0,
    )


def _episode_fact_payload(episode: Episode) -> dict[str, Any]:
    """Project an :class:`Episode` into the ``fact_payload`` dict for ADD/UPDATE."""
    payload: dict[str, Any] = {
        "id": episode.id,
        "user": episode.user,
        "agent": episode.agent,
        "content": episode.content,
        "source_node": episode.source_node,
    }
    for key in ("subject", "predicate", "object"):
        value = episode.metadata.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _coerce_replaces(raw: Any) -> list[str]:
    """Coerce ``metadata["replaces"]`` into ``list[str]`` (empty if absent/wrong shape)."""
    if not isinstance(raw, list):
        return []
    return [str(item) for item in cast("list[Any]", raw)]


def _row_to_episode(row: Any) -> Episode:
    """Decode an ``episodes`` row into an :class:`Episode`."""
    ep_id, content_blob, timestamp, metadata_blob = row
    payload_decoded = loads_jsonb(bytes(content_blob))
    payload = cast("dict[str, Any]", payload_decoded) if isinstance(payload_decoded, dict) else {}
    metadata_decoded = loads_jsonb(bytes(metadata_blob))
    metadata = (
        cast("dict[str, Any]", metadata_decoded) if isinstance(metadata_decoded, dict) else {}
    )
    return Episode(
        id=str(ep_id),
        content=str(payload.get("content", "")),
        timestamp=datetime.fromisoformat(str(timestamp)),
        source_node=str(payload.get("source_node", "")),
        agent=str(payload.get("agent", "")),
        user=str(payload.get("user", "")),
        session=str(payload.get("session", "")),
        metadata=metadata,
    )
