# SPDX-License-Identifier: Apache-2.0
"""VectorStore Protocol + Row + Hit (FR-1, FR-2, design §3.1).

Defines the structural contract every vector-store provider implements:
``bootstrap / health / migrate`` lifecycle (shared with all
``stargraph.stores`` Protocols) plus per-store CRUD (``upsert``, ``search``,
``delete``). Concrete providers (``LanceDBVectorStore`` lands later in
this spec) implement :class:`VectorStore` structurally; no inheritance
required.

:class:`Row` is the upsert payload -- ``id`` plus optional ``vector``
and ``text`` (vector-only, text-only, or both for hybrid stores) plus a
flat ``metadata`` dict restricted to JSON scalars so columnar backends
(LanceDB / Arrow) can map metadata to typed columns. :class:`Hit` is
the search result row -- ``id`` / ``score`` / ``metadata`` only;
vectors are not echoed back (callers re-fetch via ``id`` if needed).

Search ``mode`` is one of ``"vector"``, ``"fts"``, ``"hybrid"``;
providers reject combinations with missing inputs (e.g. ``mode="vector"``
without a ``vector`` argument) per design §3.1.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stargraph.stores._common import MigrationPlan, StoreHealth  # noqa: TC001

__all__ = [
    "Hit",
    "MetadataValue",
    "Row",
    "VectorStore",
]


type MetadataValue = str | int | float | bool
"""JSON-scalar value type accepted in :class:`Row` / :class:`Hit` metadata.

Restricting metadata values to scalars lets columnar backends (LanceDB /
Arrow) map metadata to typed columns without per-row schema inference.
"""


class Row(BaseModel):
    """Upsert payload accepted by :meth:`VectorStore.upsert` (design §3.1).

    At least one of ``vector`` / ``text`` must be supplied; vector-only
    rows feed pure ANN search, text-only rows feed FTS, both feed
    hybrid. ``metadata`` is restricted to JSON scalars
    (:data:`MetadataValue`) so columnar backends can store metadata as
    typed columns.
    """

    id: str
    vector: list[float] | None = None
    text: str | None = None
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)


class Hit(BaseModel):
    """Search result row returned by :meth:`VectorStore.search` (design §3.1).

    ``score`` is provider-defined (cosine similarity for ANN, BM25 for
    FTS, fused for hybrid). Vectors are not echoed; callers re-fetch by
    ``id`` if they need the raw embedding. ``metadata`` values follow
    :data:`MetadataValue` (JSON scalars only).
    """

    id: str
    score: float
    metadata: dict[str, MetadataValue]


@runtime_checkable
class VectorStore(Protocol):
    """Structural contract for vector-store providers (design §3.1).

    Implementations: :class:`stargraph.stores.lancedb.LanceDBVectorStore`
    (arrives later in this spec). Lifecycle (``bootstrap`` / ``health``
    / ``migrate``) shared with every ``stargraph.stores`` Protocol; per-store
    CRUD (``upsert`` / ``search`` / ``delete``) is vector-specific.

    Decorated with :func:`typing.runtime_checkable` so call-site
    ``isinstance(provider, VectorStore)`` checks succeed for any class
    that structurally satisfies the contract -- no inheritance required
    (NFR-3).
    """

    async def bootstrap(self) -> None:
        """Idempotent schema/metadata bootstrap (FR-8 embed-hash gate)."""
        ...

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (FR-9 fs-type / lock-state)."""
        ...

    async def migrate(self, plan: MigrationPlan) -> None:
        """Apply a :class:`MigrationPlan`; v1 supports ``add_column`` only."""
        ...

    async def upsert(self, rows: list[Row]) -> None:
        """Insert-or-replace ``rows`` by ``id``; never accepts a single Row."""
        ...

    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,  # noqa: A002
        k: int = 10,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]:
        """Return top-``k`` :class:`Hit` rows for ``mode`` (FR-16 hybrid)."""
        ...

    async def delete(self, ids: list[str]) -> int:
        """Delete rows by ``id``; return the number of rows actually deleted."""
        ...
