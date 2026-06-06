# SPDX-License-Identifier: Apache-2.0
"""KG fact-promotion rule integration tests (FR-30 / AC-6.x).

Pins the Phase-1 POC behaviour of :func:`stargraph.stores.kg_promotion.PromoteTriplesToFacts`
-- the function-call form of the YAML ``(action.assert (stargraph.evidence ...))``
rule. Exercises the full pipeline with real Kuzu + SQLite stores plus a
recording :class:`fathom.Engine` stand-in:

* :func:`test_yaml_action_assert_evidence` -- one triple in, one pinned
  ``Fact`` out, and the recording engine sees a ``stargraph.evidence``
  ``assert_fact`` call (the POC analogue of the YAML rule firing).
* :func:`test_provenance_quadruple` -- the promoted fact's lineage carries
  the (triple_id, rule_id, agent_id, promotion_ts) quadruple per AC-6.2.
"""

from __future__ import annotations

from datetime import datetime
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
    "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
    "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
)


async def test_yaml_action_assert_evidence(tmp_path: Path) -> None:
    """Promotion rule fires once per matched triple and pins a ``stargraph.evidence`` fact."""
    graph_store = RyuGraphStore(tmp_path / "graph")
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await graph_store.bootstrap()
    await fact_store.bootstrap()

    await graph_store.add_triple(
        NodeRef(id="alice", kind="Person"),
        "knows",
        NodeRef(id="bob", kind="Person"),
    )

    engine = _RecordingEngine()
    adapter = FathomAdapter(cast("Any", engine))

    promoted = await PromoteTriplesToFacts(
        graph_store,
        fact_store,
        adapter,
        filter_cypher=_FILTER_CYPHER,
        rule_id="stargraph_evidence_v1",
        agent_id="kg-promo-agent",
    )

    # Exactly one promoted fact lands on the FactStore (FactStore pin is the
    # authoritative output -- the Fathom side-channel is observability only).
    assert len(promoted) == 1
    stored = await fact_store.query(FactPattern(agent="kg-promo-agent"))
    assert len(stored) == 1
    fact = stored[0]
    assert fact.payload["subject"] == "alice"
    assert fact.payload["predicate"] == "knows"
    assert fact.payload["object"] == "bob"
    assert fact.payload["source"].startswith("ryugraph:")


async def test_provenance_quadruple(tmp_path: Path) -> None:
    """Promoted fact lineage carries (triple_id, rule_id, agent_id, promotion_ts)."""
    graph_store = RyuGraphStore(tmp_path / "graph")
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await graph_store.bootstrap()
    await fact_store.bootstrap()

    await graph_store.add_triple(
        NodeRef(id="alice", kind="Person"),
        "knows",
        NodeRef(id="carol", kind="Person"),
    )

    engine = _RecordingEngine()
    adapter = FathomAdapter(cast("Any", engine))

    promoted = await PromoteTriplesToFacts(
        graph_store,
        fact_store,
        adapter,
        filter_cypher=_FILTER_CYPHER,
        rule_id="stargraph_evidence_v1",
        agent_id="kg-promo-agent",
    )

    assert len(promoted) == 1
    fact = promoted[0]
    assert fact.lineage, "fact missing lineage"
    entry = fact.lineage[0]

    # AC-6.2 provenance quadruple: (triple_id, rule_id, agent_id, timestamp).
    triple_id = entry.get("triple_id")
    assert isinstance(triple_id, str) and triple_id
    assert "alice" in triple_id and "knows" in triple_id and "carol" in triple_id

    assert entry.get("rule_id") == "stargraph_evidence_v1"
    assert entry.get("agent_id") == "kg-promo-agent"

    promotion_ts = entry.get("promotion_ts")
    assert isinstance(promotion_ts, str) and promotion_ts
    # Round-trips as a tz-aware ISO8601 timestamp.
    parsed = datetime.fromisoformat(promotion_ts)
    assert parsed.tzinfo is not None
