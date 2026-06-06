# SPDX-License-Identifier: Apache-2.0
"""Each store embeds with its own embedder (FR-26, AC-4, Task 3.30).

Pins the per-store embed-isolation contract on
:class:`stargraph.nodes.retrieval.RetrievalNode`: when a fan-out runs across
stores configured with **different** embedders (different ``ndims`` /
``model_id`` / ``content_hash``), each store's vectorisation must use
**that store's own embedder**, never one borrowed from a peer branch.

The test installs two stub vector stores backed by
:class:`stargraph.stores.embeddings.FakeEmbedder` instances of differing
``ndims`` (4 vs 8). Each store's ``search`` records the query vector it
received; the assertion is that ``len(query_vec) == that_store.ndims``
for every captured call -- a cross-store re-embed (e.g. running the
ndims-4 query through the ndims-8 branch) would surface as a vector
length mismatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from stargraph.ir._models import StoreRef
from stargraph.nodes.retrieval import RetrievalNode
from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.vector import Hit

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore


pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


class _RetrievalState(BaseModel):
    query: str


class _Ctx:
    run_id: str = "no-cross-embed"


class _SpyVectorStore:
    """Vector store with an in-process embedder that records call args.

    The store embeds the incoming ``text`` query itself (mirroring how
    :class:`stargraph.stores.lancedb.LanceDBVectorStore` resolves text →
    vector when ``mode='vector'`` and only ``text`` is supplied) and
    records the ``ndims`` of the produced vector. Tests assert that the
    recorded ndims matches **this** store's embedder, never a peer's.
    """

    def __init__(self, embedder: FakeEmbedder) -> None:
        self._embedder = embedder
        self.embed_calls: list[dict[str, Any]] = []

    async def bootstrap(self) -> None:  # pragma: no cover -- not exercised
        return None

    async def health(self) -> Any:  # pragma: no cover -- not exercised
        return None

    async def migrate(self, plan: Any) -> None:  # pragma: no cover -- not exercised
        return None

    async def upsert(self, rows: list[Any]) -> None:  # pragma: no cover -- not exercised
        return None

    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,  # noqa: A002
        k: int = 10,
        mode: str = "vector",
    ) -> list[Hit]:
        # RetrievalNode supplies ``text`` (no pre-computed vector); the
        # store is responsible for vectorising with its own embedder.
        assert text is not None, "RetrievalNode should pass text=query"
        assert vector is None, "RetrievalNode must not pre-embed and forward a vector across stores"
        embedded = await self._embedder.embed([text], kind="query")
        produced_vec = embedded[0]
        self.embed_calls.append(
            {
                "model_id": self._embedder.model_id,
                "content_hash": self._embedder.content_hash,
                "ndims": len(produced_vec),
                "expected_ndims": self._embedder.ndims,
            },
        )
        return [Hit(id=f"{self._embedder.model_id}:r1", score=0.0, metadata={})]

    async def delete(self, ids: list[str]) -> int:  # pragma: no cover -- not exercised
        del ids
        return 0


async def test_each_store_uses_its_own_embedder() -> None:
    """Two stores with different-ndims embedders → each embeds with its own."""
    embedder_a = FakeEmbedder(ndims=4)
    embedder_b = FakeEmbedder(ndims=8)

    spy_a = _SpyVectorStore(embedder_a)
    spy_b = _SpyVectorStore(embedder_b)

    def _resolver(name: str) -> VectorStore | DocStore:
        if name == "small":
            return cast("VectorStore", spy_a)
        if name == "large":
            return cast("VectorStore", spy_b)
        raise KeyError(name)

    node = RetrievalNode(
        stores=[
            StoreRef(name="small", provider="lancedb"),
            StoreRef(name="large", provider="lancedb"),
        ],
        store_resolver=_resolver,
        k=5,
    )

    await node.execute(
        _RetrievalState(query="hello world"),
        cast("ExecutionContext", _Ctx()),
    )

    # Both stores should have been called exactly once.
    assert len(spy_a.embed_calls) == 1
    assert len(spy_b.embed_calls) == 1

    # Each call's produced vector ndims matches THAT store's embedder.
    call_a = spy_a.embed_calls[0]
    assert call_a["ndims"] == 4
    assert call_a["expected_ndims"] == 4

    call_b = spy_b.embed_calls[0]
    assert call_b["ndims"] == 8
    assert call_b["expected_ndims"] == 8

    # Negative pin: had RetrievalNode pre-embedded once and forwarded
    # the vector to BOTH branches, the spy's ``vector is None`` assert
    # in ``search`` would have tripped, OR the ndims would have matched
    # only one of the two stores. Both branches succeeded with their
    # own ndims, so cross-store re-embed did not occur.
