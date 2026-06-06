# SPDX-License-Identifier: Apache-2.0
"""MemoryStore Protocol + Episode + MemoryDelta union (FR-5, FR-27, FR-28, FR-29, design §3.4).

Defines the structural contract every episodic-memory provider
implements: ``bootstrap / health / migrate`` lifecycle (shared with all
``stargraph.stores`` Protocols) plus per-store CRUD (``put``, ``get``,
``recent``, ``consolidate``). Concrete providers
(``SQLiteMemoryStore`` lands later in this spec) implement
:class:`MemoryStore` structurally; no inheritance required.

:class:`Episode` is the per-write payload -- one episodic memory keyed
by ``(user, session, agent)`` 3-tuple (design §3.4 widening-read
semantics). :class:`ConsolidationRule` is the IR-declared rule body
``MemoryStore.consolidate`` runs against currently-stored episodes,
emitting Mem0-style typed :data:`MemoryDelta` entries.

:data:`MemoryDelta` is a Pydantic discriminated union over ``kind``:
``"add"`` (new fact), ``"update"`` (replaces by id list), ``"delete"``
(unpins by id list), and ``"noop"`` (audit-trail-only, preserves
unchanged for round-trip). Provenance fields (``source_episode_ids``,
``promotion_ts``, ``rule_id``, ``confidence``) are Pydantic-mandatory
on every variant so :meth:`stargraph.stores.fact.FactStore.apply_delta`
can validate at the promotion seam (design §4.2 lineage).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stargraph.stores._common import MigrationPlan, StoreHealth  # noqa: TC001

__all__ = [
    "AddDelta",
    "ConsolidationRule",
    "DeleteDelta",
    "Episode",
    "MemoryDelta",
    "MemoryStore",
    "NoopDelta",
    "UpdateDelta",
]


class Episode(BaseModel):
    """Episodic memory row written by :meth:`MemoryStore.put` (design §3.4).

    Scoped at write time by ``(user, session, agent)`` -- providers
    encode the 3-tuple as a trailing-separator scope key
    (``/user/Alice/session/S1/agent/rag/``) to prevent prefix-collision
    on ``LIKE`` scans. Reads widen by omitting the right-most fields.

    ``metadata`` stays typed as ``dict[str, Any]`` rather than the
    JSON-scalar union used by :data:`stargraph.stores.vector.MetadataValue`:
    the column round-trips through orjson JSONB (preserving nested
    dicts / lists) and the columnar restrictions that justify scalar-only
    metadata on :class:`stargraph.stores.vector.Row` do not apply (NFR-3).
    """

    id: str
    content: str
    timestamp: datetime
    source_node: str
    agent: str
    user: str
    session: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConsolidationRule(BaseModel):
    """IR-declared episodic→semantic consolidation rule (FR-28, FR-29).

    ``cadence`` carries the IR knob (``every: N`` episodes or
    ``cron: '...'``); :meth:`MemoryStore.consolidate` reuses the CLIPS
    rule scheduling in ``stargraph.fathom`` rather than introducing a
    second scheduler. ``when_filter`` is the SQL/CLIPS predicate
    selecting eligible episodes; ``then_emits`` lists the fact-channel
    names this rule promotes into.
    """

    id: str
    cadence: dict[str, Any]
    when_filter: str
    then_emits: list[str]


class AddDelta(BaseModel):
    """``ADD`` typed delta -- assert a new fact (design §3.4)."""

    kind: Literal["add"]
    fact_payload: dict[str, Any]
    source_episode_ids: list[str]
    promotion_ts: datetime
    rule_id: str
    confidence: float


class UpdateDelta(BaseModel):
    """``UPDATE`` typed delta -- replaces ``replaces`` ids with new payload."""

    kind: Literal["update"]
    replaces: list[str]
    fact_payload: dict[str, Any]
    source_episode_ids: list[str]
    promotion_ts: datetime
    rule_id: str
    confidence: float


class DeleteDelta(BaseModel):
    """``DELETE`` typed delta -- unpin every id in ``replaces``."""

    kind: Literal["delete"]
    replaces: list[str]
    source_episode_ids: list[str]
    promotion_ts: datetime
    rule_id: str
    confidence: float


class NoopDelta(BaseModel):
    """``NOOP`` typed delta -- audit-only; preserves unchanged for round-trip."""

    kind: Literal["noop"]
    source_episode_ids: list[str]
    promotion_ts: datetime
    rule_id: str
    confidence: float


MemoryDelta = Annotated[
    AddDelta | UpdateDelta | DeleteDelta | NoopDelta,
    Field(discriminator="kind"),
]
"""Mem0-style typed delta union; the only acceptable promotion path
into :class:`stargraph.stores.fact.FactStore` (design §4.2)."""


@runtime_checkable
class MemoryStore(Protocol):
    """Structural contract for episodic-memory providers (design §3.4).

    Implementations: :class:`stargraph.stores.sqlite_memory.SQLiteMemoryStore`
    (arrives later in this spec). Lifecycle (``bootstrap`` / ``health``
    / ``migrate``) shared with every ``stargraph.stores`` Protocol; per-store
    CRUD (``put`` / ``get`` / ``recent`` / ``consolidate``) is
    memory-specific.
    """

    async def bootstrap(self) -> None:
        """Idempotent schema/metadata bootstrap (FR-8 inherited)."""
        ...

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (FR-9 fs-type / lock-state)."""
        ...

    async def migrate(self, plan: MigrationPlan) -> None:
        """Apply a :class:`MigrationPlan`; v1 supports ``add_column`` only."""
        ...

    async def put(
        self,
        episode: Episode,
        *,
        user: str,
        session: str,
        agent: str,
    ) -> None:
        """Insert ``episode`` keyed at ``(user, session, agent)``.

        Scope passed as keyword-only args (rather than the older 3-tuple)
        so widening-read on :meth:`recent` can omit ``session`` / ``agent``
        without ad-hoc ``Optional`` slot conventions inside a tuple.
        """
        ...

    async def recent(
        self,
        user: str,
        session: str | None = None,
        agent: str | None = None,
        *,
        limit: int,
    ) -> list[Episode]:
        """Return the most-recent ``limit`` episodes matching the widening scope.

        ``session`` / ``agent`` default to ``None`` (wildcard) so widening
        reads (FR-27) can ask for every session under a user, etc.
        """
        ...

    async def consolidate(self, rule: ConsolidationRule) -> list[MemoryDelta]:
        """Run ``rule`` against stored episodes; emit Mem0-style typed deltas."""
        ...
