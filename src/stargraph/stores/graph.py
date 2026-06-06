# SPDX-License-Identifier: Apache-2.0
"""GraphStore Protocol + NodeRef / GraphPath / ResultSet records (FR-1, FR-3, design §3.2).

Defines the property-graph storage contract Knowledge layer uses for
triple writes, Cypher queries, and bounded neighbourhood expansion.
Providers (RyuGraphStore -- the community fork of Kuzu after its
2025-10-10 archival) implement :class:`GraphStore` structurally; no
inheritance required.

The Protocol mirrors the :mod:`stargraph.checkpoint.protocol` shape --
``bootstrap / health / migrate`` lifecycle plus per-store CRUD
(``add_triple``, ``query``, ``expand``) -- and ships three Pydantic
records used across every provider:

- :class:`NodeRef` -- ``(id, kind)`` pair identifying a node without
  payload, used as ``add_triple`` / ``expand`` arguments.
- :class:`GraphPath` -- variable-length walk return value carrying
  parallel ``nodes`` and ``edges`` sequences (named ``GraphPath`` rather
  than ``Path`` to avoid clashing with :class:`pathlib.Path`).
- :class:`ResultSet` -- generic Cypher result envelope: ``rows``
  (list of column-keyed dicts) plus ``columns`` (declared order).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence  # noqa: TC003
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stargraph.stores._common import MigrationPlan, StoreHealth  # noqa: TC001

__all__ = [
    "GraphPath",
    "GraphStore",
    "NodeRef",
    "ResultSet",
]


class NodeRef(BaseModel):
    """Identifier-plus-kind handle for a graph node (design §3.2).

    Carries no payload; `kind` distinguishes node tables / labels in
    portable Cypher (``Entity`` is the v1 default; future schemas may
    layer additional kinds via migrations).
    """

    id: str
    kind: str


class GraphPath(BaseModel):
    """Walk returned by :meth:`GraphStore.expand` (design §3.2).

    ``nodes`` and ``edges`` are parallel: ``nodes[0]`` is the start
    node, ``edges[i]`` connects ``nodes[i]`` to ``nodes[i+1]``, so
    ``len(edges) == len(nodes) - 1`` for any non-empty walk.

    Named ``GraphPath`` rather than ``Path`` to avoid clashing with
    :class:`pathlib.Path` at import sites.
    """

    nodes: list[NodeRef]
    edges: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])


class ResultSet(BaseModel):
    """Cypher result envelope returned by :meth:`GraphStore.query` (design §3.2).

    ``rows`` are column-keyed dicts; ``columns`` preserves the declared
    ``RETURN`` order so callers can render tabular output deterministically.

    Row values stay typed as :class:`Any` rather than the JSON-scalar union
    used by :data:`stargraph.stores.vector.MetadataValue`: Cypher ``RETURN``
    expressions legitimately yield node maps (``{id, kind, ...}``), edge
    maps, and ``nodes(p)`` / ``rels(p)`` lists when callers project paths,
    none of which fit a scalar-only union (NFR-3).
    """

    rows: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    columns: list[str] = Field(default_factory=list[str])


@runtime_checkable
class GraphStore(Protocol):
    """Property-graph storage contract (design §3.2).

    Implementations: :class:`stargraph.stores.ryugraph.RyuGraphStore`
    (native ``ryugraph.AsyncConnection``, single-writer multi-reader).
    Cypher passed to :meth:`query` must satisfy the portable subset
    enforced by :mod:`stargraph.stores.cypher` -- providers reject
    out-of-subset input with ``UnportableCypherError``.

    ``expand`` interpolates ``hops`` as a Cypher literal (variable-length
    bounds cannot be parameterised); providers validate ``0 < hops <= 10``
    and document walk-vs-trail semantics at the Protocol level (AC-9.5).

    Walk vs trail (AC-9.5): variable-length matches in Stargraph's portable
    Cypher subset return *walks* -- vertices and edges may repeat, so the
    same query may return a different number of paths across providers.
    RyuGraph always returns walk semantics (no ``is_trail`` filter; trail
    filtering is provider-specific and RyuGraph-only callers must apply
    it themselves at the row level). Neo4j 5 Cypher under the same shape
    returns *trails* (edges unique). Callers MUST treat the row count as
    provider-dependent for any pattern that can re-traverse an edge.
    """

    async def bootstrap(self) -> None:
        """Idempotent schema-on-first-write bootstrap (FR-3)."""
        ...

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot."""
        ...

    async def migrate(self, plan: MigrationPlan) -> None:
        """Apply a :class:`MigrationPlan` to the underlying schema."""
        ...

    async def add_triple(
        self,
        s: NodeRef,
        p: str,
        o: NodeRef,
        *,
        props: Mapping[str, str] | None = None,
    ) -> None:
        """Upsert a single ``(s)-[p]->(o)`` triple with optional edge ``props``."""
        ...

    async def query(self, cypher: str, params: Mapping[str, Any] | None = None) -> ResultSet:
        """Execute a portable-subset Cypher ``cypher`` with bound ``params``."""
        ...

    async def expand(
        self,
        node: NodeRef,
        hops: int = 1,
        *,
        predicates: Sequence[str] | None = None,
    ) -> list[GraphPath]:
        """Return walks starting at ``node`` up to ``hops`` deep, optionally
        filtering by ``predicates`` on each edge."""
        ...
