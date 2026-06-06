# SPDX-License-Identifier: Apache-2.0
"""One-way promotion invariant tests (FR-30 / AC-6.x).

Pins the design §3.13 invariant that triple → fact promotion is **one-way**:
deleting a triple from the :class:`GraphStore` after promotion does NOT
auto-retract the corresponding pinned :class:`Fact`. Callers that want
bidirectional linkage must invoke :meth:`FactStore.unpin` (or apply a
:class:`DeleteDelta`) explicitly.

* :func:`test_triple_delete_does_not_unpin_fact` -- promote a triple, delete
  it from the underlying Kuzu graph, and confirm the pinned fact remains.
* :func:`test_explicit_retraction_unpins_fact` -- after pinning, an explicit
  :meth:`FactStore.unpin` call removes the fact (the supported retraction
  path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from stargraph.fathom import FathomAdapter
from stargraph.stores.fact import FactPattern
from stargraph.stores.graph import NodeRef
from stargraph.stores.kg_promotion import PromoteTriplesToFacts
from stargraph.stores.ryugraph import RyuGraphStore
from stargraph.stores.sqlite_fact import SQLiteFactStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class _RecordingEngine:
    """Minimal ``fathom.Engine`` stand-in -- records ``assert_fact`` calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


_FILTER_CYPHER = (
    "MATCH (s:Entity {id: 'alice'})-[r:Rel]->(o:Entity) "
    "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
)


async def _seed_alice_knows_bob(graph_store: RyuGraphStore) -> None:
    await graph_store.add_triple(
        NodeRef(id="alice", kind="Person"),
        "knows",
        NodeRef(id="bob", kind="Person"),
    )


async def test_triple_delete_does_not_unpin_fact(tmp_path: Path) -> None:
    """Triple deletion in the GraphStore must not retract the pinned Fact."""
    graph_store = RyuGraphStore(tmp_path / "graph")
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await graph_store.bootstrap()
    await fact_store.bootstrap()

    await _seed_alice_knows_bob(graph_store)

    adapter = FathomAdapter(cast("Any", _RecordingEngine()))

    # 1. Promote -> fact pinned.
    promoted = await PromoteTriplesToFacts(
        graph_store,
        fact_store,
        adapter,
        filter_cypher=_FILTER_CYPHER,
        rule_id="stargraph_evidence_v1",
        agent_id="one-way-agent",
    )
    assert len(promoted) == 1
    pre_delete = await fact_store.query(FactPattern(agent="one-way-agent"))
    assert len(pre_delete) == 1

    # 2. Delete the triple from the GraphStore. The public ``query`` rejects
    #    writes via the Linter, so issue the DELETE through the underlying
    #    Kuzu ``AsyncConnection`` directly (matches how ``add_triple``
    #    bypasses the linter for parameterised mutations).
    conn = graph_store._require_conn()  # pyright: ignore[reportPrivateUsage]
    await conn.execute("MATCH (s:Entity)-[r:Rel]->(o:Entity) DELETE r")

    # Sanity: the triple is gone from the graph.
    rs = await graph_store.query("MATCH (s:Entity)-[r:Rel]->(o:Entity) RETURN s.id AS subject")
    assert rs.rows == []

    # 3. Fact REMAINS in the FactStore (one-way invariant).
    post_delete = await fact_store.query(FactPattern(agent="one-way-agent"))
    assert len(post_delete) == 1
    assert post_delete[0].id == promoted[0].id


async def test_explicit_retraction_unpins_fact(tmp_path: Path) -> None:
    """Explicit ``FactStore.unpin`` is the supported retraction path."""
    graph_store = RyuGraphStore(tmp_path / "graph")
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await graph_store.bootstrap()
    await fact_store.bootstrap()

    await _seed_alice_knows_bob(graph_store)

    adapter = FathomAdapter(cast("Any", _RecordingEngine()))

    # 1. Pin via promotion.
    promoted = await PromoteTriplesToFacts(
        graph_store,
        fact_store,
        adapter,
        filter_cypher=_FILTER_CYPHER,
        rule_id="stargraph_evidence_v1",
        agent_id="retract-agent",
    )
    assert len(promoted) == 1
    fact_id = promoted[0].id
    assert len(await fact_store.query(FactPattern(agent="retract-agent"))) == 1

    # 2. Run explicit retraction (analogue of a separate retraction rule).
    await fact_store.unpin(fact_id)

    # 3. Fact is gone.
    assert await fact_store.query(FactPattern(agent="retract-agent")) == []
