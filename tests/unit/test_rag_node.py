# SPDX-License-Identifier: Apache-2.0
"""``kind: rag`` unit tests -- lexical scorer + RagNode over fake stores.

The inner answer node is a stub :class:`NodeBase` (no dspy import); the
stores are in-memory Protocol fakes, so these tests pin the node's own
logic: ranking, RRF fusion across branches, content resolution order,
the zero-hit short-circuit, and loud runtime failures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from stargraph.errors import StargraphRuntimeError
from stargraph.nodes.rag import RagNode, RagNodeConfig, RagStoreBinding, lexical_score
from stargraph.stores._common import MigrationPlan, StoreHealth
from stargraph.stores.doc import Document
from stargraph.stores.vector import Hit

pytestmark = pytest.mark.unit


# ----------------------------------------------------------- lexical_score


def test_lexical_score_zero_without_overlap() -> None:
    assert lexical_score("alpha beta", "gamma delta") == 0.0


def test_lexical_score_rewards_distinct_terms_over_repetition() -> None:
    distinct = lexical_score("alpha beta", "alpha beta")
    repeated = lexical_score("alpha beta", "alpha alpha alpha alpha")
    assert distinct > repeated


def test_lexical_score_case_insensitive_and_deterministic() -> None:
    a = lexical_score("Alpha", "ALPHA alpha")
    b = lexical_score("alpha", "alpha Alpha")
    assert a == b > 0.0


# ----------------------------------------------------------- fakes


class _FakeDocStore:
    def __init__(self, docs: dict[str, str]) -> None:
        self._docs = docs
        self.bootstrapped = 0

    async def bootstrap(self) -> None:
        self.bootstrapped += 1

    async def health(self) -> StoreHealth:
        return StoreHealth(ok=True, version=1, fs_type="fake", lock_state="free")

    async def migrate(self, plan: MigrationPlan) -> None:
        del plan

    async def put(
        self, doc_id: str, content: str | bytes, *, metadata: dict[str, Any] | None = None
    ) -> None:
        del metadata
        self._docs[doc_id] = content if isinstance(content, str) else ""

    async def get(self, doc_id: str) -> Document | None:
        text = self._docs.get(doc_id)
        if text is None:
            return None
        return Document(id=doc_id, content=text, created_at=datetime.now(UTC))

    async def query(
        self,
        filter: str | None = None,  # noqa: A002
        *,
        limit: int = 100,
    ) -> list[Document]:
        del filter
        now = datetime.now(UTC)
        return [
            Document(id=doc_id, content=text, created_at=now)
            for doc_id, text in list(self._docs.items())[:limit]
        ]


class _FakeVectorStore:
    """Minimal search-only stand-in for the lancedb branch."""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits
        self.calls: list[tuple[str, int]] = []

    async def search(self, *, text: str, k: int) -> list[Hit]:
        self.calls.append((text, k))
        return self._hits[:k]


class _EchoAnswerNode:
    """Inner stub: proves which question/context the rag node passed in."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    async def execute(self, state: BaseModel, ctx: Any) -> dict[str, Any]:
        del ctx
        question = str(getattr(state, "question"))  # noqa: B009
        context = str(getattr(state, "context"))  # noqa: B009
        self.seen.append((question, context))
        return {"answer": f"answered:{question}", "reasoning": "internal"}


class _State(BaseModel):
    query: str = "how do stars form"


def _node(
    *,
    docs: dict[str, str] | None = None,
    vector_hits: list[Hit] | None = None,
    inner: Any = None,
    **cfg_overrides: object,
) -> tuple[RagNode, _EchoAnswerNode, _FakeDocStore | None]:
    config = RagNodeConfig(
        stores=[RagStoreBinding(provider="sqlite_doc", path="unused.db")],
        **cfg_overrides,  # pyright: ignore[reportArgumentType]
    )
    answer_node = inner if inner is not None else _EchoAnswerNode()
    doc_store = _FakeDocStore(docs) if docs is not None else None
    node = RagNode(
        inner=answer_node,  # pyright: ignore[reportArgumentType]
        config=config,
        doc_stores=[doc_store] if doc_store is not None else [],  # pyright: ignore[reportArgumentType]
        vector_stores=[_FakeVectorStore(vector_hits)] if vector_hits is not None else [],  # pyright: ignore[reportArgumentType]
    )
    return node, answer_node, doc_store


_CTX: Any = SimpleNamespace(run_id="run-rag")


async def test_doc_store_lexical_ranking_and_citations() -> None:
    node, answer_node, doc_store = _node(
        docs={
            "doc-molecular": "how stars form: molecular clouds collapse under gravity",
            "doc-cooking": "how to form perfect pasta shapes",
            "doc-irrelevant": "quarterly report on cheese exports",
        },
        k=2,
    )

    out = await node.execute(_State(), _CTX)

    assert out["answer"] == "answered:how do stars form"
    # Best lexical match first; the zero-score doc never appears.
    assert out["citations"][0] == "doc-molecular"
    assert "doc-irrelevant" not in out["citations"]
    # The context block carries [id] excerpts from the ranked docs.
    question, context = answer_node.seen[0]
    assert question == "how do stars form"
    assert context.startswith("[doc-molecular] how stars form: molecular clouds")
    # "reasoning" from the inner CoT is whitelisted away.
    assert set(out) == {"answer", "citations"}
    assert doc_store is not None and doc_store.bootstrapped == 1


async def test_zero_hits_short_circuits_without_llm() -> None:
    inner = _EchoAnswerNode()
    node, _, _ = _node(docs={"doc-1": "nothing relevant here at all"}, inner=inner)

    out = await node.execute(_State(query="xylophone zymurgy"), _CTX)

    assert out == {"answer": "", "citations": []}
    assert inner.seen == []  # the LM was never consulted


async def test_vector_branch_fuses_and_resolves_content_via_doc_get() -> None:
    node, answer_node, _ = _node(
        docs={"doc-vec": "vector-indexed doc about star formation"},
        vector_hits=[Hit(id="doc-vec", score=9.0, metadata={})],
        k=3,
    )

    out = await node.execute(_State(), _CTX)

    # Same id from both branches: fused once, content resolved.
    assert out["citations"].count("doc-vec") == 1
    _, context = answer_node.seen[0]
    assert "[doc-vec] vector-indexed doc about star formation" in context


async def test_vector_only_content_falls_back_to_metadata_text() -> None:
    node, answer_node, _ = _node(
        vector_hits=[Hit(id="row-1", score=1.0, metadata={"text": "metadata carried body"})],
    )

    await node.execute(_State(), _CTX)

    _, context = answer_node.seen[0]
    assert context == "[row-1] metadata carried body"


async def test_missing_query_field_fails_loud() -> None:
    class NoQuery(BaseModel):
        other: str = ""

    node, _, _ = _node(docs={})

    with pytest.raises(StargraphRuntimeError, match="does not exist on the run state"):
        await node.execute(NoQuery(), _CTX)


async def test_scan_limit_caps_doc_scan() -> None:
    docs = {f"doc-{i}": "stars everywhere stars" for i in range(20)}
    node, _, _ = _node(docs=docs, scan_limit=3, k=10)

    out = await node.execute(_State(query="stars"), _CTX)

    assert len(out["citations"]) == 3
