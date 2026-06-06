# SPDX-License-Identifier: Apache-2.0
"""FactStore Protocol + Fact + FactPattern (FR-6, design §3.5).

Defines the structural contract every semantic-fact provider implements:
``bootstrap / health / migrate`` lifecycle (shared with all
``stargraph.stores`` Protocols) plus per-store CRUD (``pin``, ``query``,
``unpin``). Concrete providers (``SQLiteFactStore`` lands later in this
spec) implement :class:`FactStore` structurally; no inheritance required.

:class:`Fact` is the semantic-memory row -- session-independent
``(user, agent)`` scoping diverges from
:class:`stargraph.stores.memory.MemoryStore` 3-tuple (design §3.5). Every
``Fact`` carries Pydantic-mandatory ``lineage`` linking back to the
originating episodes / triples (design §4.2 -- lineage is NEVER
optional, enforced by AST-walker test inherited from engine NFR-6).

:class:`FactPattern` is the structural query payload accepted by
:meth:`FactStore.query` -- subject / predicate / object triple slots
plus optional ``user`` / ``agent`` scope filters.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stargraph.stores._common import MigrationPlan, StoreHealth  # noqa: TC001

__all__ = [
    "Fact",
    "FactPattern",
    "FactStore",
]


class Fact(BaseModel):
    """Semantic-memory row pinned by :meth:`FactStore.pin` (design §3.5).

    Scoped at ``(user, agent)`` (semantic facts session-independent).
    ``lineage`` is mandatory -- every ``Fact`` traces back to the
    originating episode ids / triple ids / rule firings (design §4.2).

    ``payload`` / ``lineage`` / ``metadata`` stay typed with
    ``dict[str, Any]`` (rather than the JSON-scalar union used by
    :data:`stargraph.stores.vector.MetadataValue`): all three round-trip
    through orjson JSONB and routinely carry nested dicts / lists --
    lineage rows in particular are nested ``{"kind", "source_id", ...}``
    objects per design §4.2, which a scalar-only union could not
    express (NFR-3).
    """

    id: str
    user: str
    agent: str
    payload: dict[str, Any]
    lineage: list[dict[str, Any]]
    confidence: float
    pinned_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class FactPattern(BaseModel):
    """Structural query pattern accepted by :meth:`FactStore.query`.

    Subject / predicate / object slots default to ``None`` (wildcard).
    ``user`` / ``agent`` slots filter by the ``(user, agent)`` scope
    (semantic facts session-independent -- diverges from MemoryStore).
    """

    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    user: str | None = None
    agent: str | None = None


@runtime_checkable
class FactStore(Protocol):
    """Structural contract for semantic-fact providers (design §3.5).

    Implementations: :class:`stargraph.stores.sqlite_fact.SQLiteFactStore`
    (arrives later in this spec). Lifecycle (``bootstrap`` / ``health``
    / ``migrate``) shared with every ``stargraph.stores`` Protocol; per-store
    CRUD (``pin`` / ``query`` / ``unpin``) is fact-specific.
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

    async def pin(self, fact: Fact) -> None:
        """Persist ``fact`` (insert-or-replace by ``fact.id``)."""
        ...

    async def query(self, pattern: FactPattern) -> list[Fact]:
        """Return every :class:`Fact` matching ``pattern``."""
        ...

    async def unpin(self, fact_id: str) -> None:
        """Remove the fact identified by ``fact_id``."""
        ...
