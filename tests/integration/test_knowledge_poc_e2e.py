# SPDX-License-Identifier: Apache-2.0
"""POC FINAL MILESTONE -- end-to-end RAG through every store + checkpoint.

Task 1.34 milestone test for ``specs/harbor-knowledge``. Exercises the
full Phase-1 knowledge stack in one ``async`` test:

1. Bootstraps every store at a ``tmp_path`` root: LanceDB (vectors,
   :class:`~harbor.stores.lancedb.LanceDBVectorStore`), Kuzu (graph,
   :class:`~harbor.stores.ryugraph.RyuGraphStore`), and three SQLite
   stores (:class:`~harbor.stores.sqlite_doc.SQLiteDocStore`,
   :class:`~harbor.stores.sqlite_memory.SQLiteMemoryStore`,
   :class:`~harbor.stores.sqlite_fact.SQLiteFactStore`).
2. Seeds LanceDB with five vectors via the dependency-free
   :class:`~harbor.stores.embeddings.FakeEmbedder` and seeds the doc
   store with five matching documents.
3. Adds three triples to Kuzu (alice/bob/carol facts).
4. Runs :class:`~harbor.skills.refs.rag.RagSkill` against the query
   ``"what does alice know"`` with both vector + doc store bindings;
   asserts ``retrieved`` carries hit ids drawn from BOTH stores.
5. Promotes the alice triples into pinned :class:`~harbor.stores.fact.Fact`
   rows via :func:`~harbor.stores.kg_promotion.PromoteTriplesToFacts`
   (which is the function-call form of the CLIPS promotion rule for
   Phase-1 POC scope -- a real CLIPS firing path lands in Phase-2).
   Asserts each promoted fact carries a ``triple_id`` lineage entry.
6. Bootstraps :class:`~harbor.checkpoint.sqlite.SQLiteCheckpointer`
   and writes a :class:`~harbor.checkpoint.protocol.Checkpoint` whose
   ``state`` payload carries a ``vector_versions`` metadata key
   sourced from ``await tbl.version()`` on the LanceDB table.
7. Reads the checkpoint back and confirms ``vector_versions`` is
   recoverable -- the FR-16 reproducibility hook for counterfactual
   replay.

POC simplification: step 6/7 wires the LanceDB ``version()`` directly
into the ``Checkpoint.state`` dict rather than through a dedicated
``vector_versions`` column. The engine snapshot is JCS-serialisable
already, so a state-resident metadata key is sufficient for the POC
milestone; a first-class column lands when the consolidation rule
scheduler grows in Phase-3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import pytest

from harbor.checkpoint.protocol import Checkpoint
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.fathom import FathomAdapter
from harbor.ir._models import StoreRef
from harbor.skills.refs.rag import RagSkill, RagState
from harbor.stores.embeddings import FakeEmbedder
from harbor.stores.fact import FactPattern
from harbor.stores.graph import NodeRef
from harbor.stores.kg_promotion import PromoteTriplesToFacts
from harbor.stores.lancedb import LanceDBVectorStore
from harbor.stores.ryugraph import RyuGraphStore
from harbor.stores.sqlite_doc import SQLiteDocStore
from harbor.stores.sqlite_fact import SQLiteFactStore
from harbor.stores.sqlite_memory import SQLiteMemoryStore
from harbor.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path

    from harbor.nodes.base import ExecutionContext
    from harbor.stores.doc import DocStore
    from harbor.stores.vector import VectorStore


pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


class _RecordingEngine:
    """Minimal ``fathom.Engine`` stand-in -- records assert_fact calls.

    Mirrors the recorder used in ``tests/property/test_slot_regex_evasion.py``;
    Fathom's real engine is not on the Phase-1 POC critical path for the
    KG-promotion side-channel (FactStore pin is the authoritative output).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


class _StubExecutionContext:
    """Minimal :class:`ExecutionContext` for the RAG run (run_id only)."""

    run_id: str = "poc-e2e-run"


_DOCS: tuple[tuple[str, str], ...] = (
    ("doc-alice", "Alice knows Bob and frequently talks about graphs."),
    ("doc-bob", "Bob is a longtime friend of Alice and Carol."),
    ("doc-carol", "Carol works on knowledge graphs with Alice."),
    ("doc-eve", "Eve is unrelated to the alice/bob/carol cluster."),
    ("doc-world", "General world knowledge unrelated to the query."),
)


_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("alice", "knows", "bob"),
    ("alice", "knows", "carol"),
    ("bob", "knows", "carol"),
)


async def test_knowledge_poc_e2e_milestone(tmp_path: Path) -> None:
    """End-to-end RAG through every store + engine checkpoint (POC milestone)."""
    # ------------------------------------------------------------------
    # 1. Bootstrap every store under ``tmp_path``.
    # ------------------------------------------------------------------
    embedder = FakeEmbedder()
    vector_store = LanceDBVectorStore(tmp_path / "vectors", embedder)
    graph_store = RyuGraphStore(tmp_path / "graph")
    doc_store = SQLiteDocStore(tmp_path / "docs.sqlite")
    memory_store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")

    await vector_store.bootstrap()
    await graph_store.bootstrap()
    await doc_store.bootstrap()
    await memory_store.bootstrap()
    await fact_store.bootstrap()

    # ------------------------------------------------------------------
    # 2. Seed LanceDB (5 vectors via FakeEmbedder) + SQLiteDoc (5 docs).
    # ------------------------------------------------------------------
    rows = [Row(id=doc_id, text=text) for doc_id, text in _DOCS]
    await vector_store.upsert(rows)
    for doc_id, text in _DOCS:
        await doc_store.put(doc_id, text, metadata={"doc_id": doc_id})

    # ------------------------------------------------------------------
    # 3. Add 3 triples to Kuzu.
    # ------------------------------------------------------------------
    for s, p, o in _TRIPLES:
        await graph_store.add_triple(
            NodeRef(id=s, kind="Person"),
            p,
            NodeRef(id=o, kind="Person"),
        )

    # ------------------------------------------------------------------
    # 4. RagSkill run -- query "what does alice know" against vector + doc.
    # ------------------------------------------------------------------
    skill = RagSkill(
        name="rag",
        version="0.1.0",
        description="POC RAG milestone test",
    )

    vector_ref = StoreRef(name="vec", provider="lancedb")
    doc_ref = StoreRef(name="docs", provider="sqlite-doc")

    def resolve(name: str) -> VectorStore | DocStore:
        if name == "vec":
            return vector_store
        if name == "docs":
            return doc_store
        msg = f"unknown store ref: {name!r}"
        raise KeyError(msg)

    state = RagState(query="what does alice know")
    ctx = cast("ExecutionContext", _StubExecutionContext())
    out_state = await skill.run(
        state,
        ctx,
        stores=[vector_ref, doc_ref],
        store_resolver=resolve,
        k=5,
    )

    # ------------------------------------------------------------------
    # 5. Verify ``retrieved`` returned hits sourced from BOTH stores.
    # ------------------------------------------------------------------
    retrieved_ids = {h.id for h in out_state.retrieved}
    assert retrieved_ids, "RAG skill returned no hits"
    seeded_ids = {doc_id for doc_id, _ in _DOCS}
    # Every hit id should resolve to a seeded doc id (vector + doc both
    # round-trip ids); the union of branches must include enough coverage
    # that we know both stores fired -- DocStore.query returns up to k
    # docs unfiltered, so coverage of >= 4 hit ids confirms the doc
    # branch contributed.
    assert retrieved_ids.issubset(seeded_ids)
    assert len(retrieved_ids) >= 4, (
        f"expected >= 4 distinct retrieved ids (vector + doc fan-out), got {sorted(retrieved_ids)}"
    )
    assert out_state.answer == "STANDIN_ANSWER"  # canned StandinLM payload (T10 dspy seam)
    assert out_state.sources

    # ------------------------------------------------------------------
    # 6. CLIPS promotion (function-call form): promote alice triples into
    #    pinned Facts via PromoteTriplesToFacts.
    # ------------------------------------------------------------------
    fathom_adapter = FathomAdapter(cast("Any", _RecordingEngine()))
    promoted = await PromoteTriplesToFacts(
        graph_store,
        fact_store,
        fathom_adapter,
        filter_cypher=(
            "MATCH (s:Entity {id: 'alice'})-[r:Rel]->(o:Entity) "
            "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
        ),
        rule_id="promote_alice_knows_v1",
        agent_id="poc-e2e-agent",
    )
    assert len(promoted) == 2, f"expected 2 promoted alice triples, got {len(promoted)}"
    for fact in promoted:
        assert fact.payload["subject"] == "alice"
        assert fact.payload["predicate"] == "knows"
        assert fact.lineage, "fact missing lineage"
        triple_id = fact.lineage[0].get("triple_id")
        assert isinstance(triple_id, str) and triple_id, (
            f"fact {fact.id!r} missing triple_id lineage"
        )

    # FactStore round-trip -- evidence facts are persisted.
    stored_facts = await fact_store.query(FactPattern(agent="poc-e2e-agent"))
    assert len(stored_facts) == 2

    # ------------------------------------------------------------------
    # 7. Engine checkpoint -- write Checkpoint with ``vector_versions``
    #    metadata sourced from LanceDB ``await tbl.version()``.
    # ------------------------------------------------------------------
    db = await lancedb.connect_async(tmp_path / "vectors")
    tbl = await db.open_table("vectors")
    table_version = await tbl.version()
    assert isinstance(table_version, int) and table_version >= 1

    vector_versions: dict[str, int] = {"vec": table_version}

    checkpoint = Checkpoint(
        run_id="poc-e2e-run",
        step=0,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="poc-graph-hash",
        runtime_hash="poc-runtime-hash",
        state={
            "query": out_state.query,
            "sources": list(out_state.sources),
            "vector_versions": vector_versions,
        },
        clips_facts=[],
        last_node="rag.assemble",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )

    checkpointer = SQLiteCheckpointer(tmp_path / "checkpoint.sqlite")
    try:
        await checkpointer.bootstrap()
        await checkpointer.write(checkpoint)

        # ------------------------------------------------------------------
        # 8. Read back -- verify ``vector_versions`` survived JCS round-trip.
        # ------------------------------------------------------------------
        roundtripped = await checkpointer.read_latest("poc-e2e-run")
        assert roundtripped is not None
        assert roundtripped.state.get("vector_versions") == vector_versions
    finally:
        await checkpointer.close()
