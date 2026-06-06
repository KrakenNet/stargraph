# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for ``RagSkill`` against ephemeral LanceDB + SQLiteDoc.

Task 3.42 / FR-32 / AC-7.1 / NFR-4. Bootstraps real LanceDB and
SQLiteDoc stores under ``tmp_path``, seeds them, drives
:class:`stargraph.skills.refs.rag.RagSkill.run`, and asserts the
retrieved hits, answer string, and sources list all round-trip
through both branches without any live network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from stargraph.ir._models import StoreRef
from stargraph.skills.refs.rag import RagSkill, RagState
from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.sqlite_doc import SQLiteDocStore
from stargraph.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path

    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore


pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


class _StubCtx:
    run_id: str = "rag-e2e-run"


_DOCS: tuple[tuple[str, str], ...] = (
    ("doc-a", "Alice writes about graph databases."),
    ("doc-b", "Bob curates retrieval pipelines."),
    ("doc-c", "Carol benchmarks embedders."),
)


async def test_rag_reference_skill_e2e(tmp_path: Path) -> None:
    """RagSkill drives retrieve→llm-stub→assemble against real stores."""
    embedder = FakeEmbedder()
    vector_store = LanceDBVectorStore(tmp_path / "vec", embedder)
    doc_store = SQLiteDocStore(tmp_path / "docs.sqlite")

    await vector_store.bootstrap()
    await doc_store.bootstrap()

    rows = [Row(id=did, text=text) for did, text in _DOCS]
    await vector_store.upsert(rows)
    for did, text in _DOCS:
        await doc_store.put(did, text, metadata={"doc_id": did})

    skill = RagSkill(
        name="rag",
        version="0.1.0",
        description="E2E rag reference test",
    )

    vec_ref = StoreRef(name="vec", provider="lancedb")
    doc_ref = StoreRef(name="docs", provider="sqlite-doc")

    def resolve(name: str) -> VectorStore | DocStore:
        if name == "vec":
            return vector_store
        if name == "docs":
            return doc_store
        raise KeyError(name)

    state = RagState(query="who writes about graphs")
    ctx = cast("ExecutionContext", _StubCtx())
    out = await skill.run(
        state,
        ctx,
        stores=[vec_ref, doc_ref],
        store_resolver=resolve,
        k=3,
    )

    # FR-32 / AC-7.1: retrieve→assemble round-trips real provider IDs.
    assert out.retrieved, "RagSkill returned no hits"
    seeded = {did for did, _ in _DOCS}
    retrieved_ids = {h.id for h in out.retrieved}
    assert retrieved_ids.issubset(seeded)

    # ``sources`` mirrors retrieved hit ids one-to-one (provenance link).
    assert out.sources == [h.id for h in out.retrieved]

    # POC LLM stub format: deterministic answer string keyed off hit count.
    assert out.answer == "STANDIN_ANSWER"  # canned StandinLM payload (T10 dspy seam)

    # context_window non-empty (NFR-4: loud-fail if assemble dropped hits).
    assert out.context_window
    for h in out.retrieved:
        assert f"[{h.id}]" in out.context_window
