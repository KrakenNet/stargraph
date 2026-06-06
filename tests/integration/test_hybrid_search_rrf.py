# SPDX-License-Identifier: Apache-2.0
"""Hybrid search RRF integration test (FR-16, AC-4.4).

Pins :class:`~stargraph.stores.lancedb.LanceDBVectorStore` ``mode='hybrid'``
to the Reciprocal Rank Fusion contract from
:class:`~stargraph.stores.rerankers.RRFReranker`. The vector + fts branches
are run independently first; the hybrid branch is then asserted to be
the rank-by-rank RRF fusion of the two.

Determinism: :class:`~stargraph.stores.embeddings.FakeEmbedder` (``ndims=4``).
Six rows are seeded with overlapping text + vectors so both retrieval
branches return non-empty hits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.rerankers import RRFReranker
from stargraph.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_NDIMS = 4
_K = 5


def _seed_rows() -> list[Row]:
    """Six rows mixing the query terms across distinct ids."""
    return [
        Row(id="r1", text="alpha quick brown fox", metadata={"tag": "a"}),
        Row(id="r2", text="alpha lazy dog", metadata={"tag": "a"}),
        Row(id="r3", text="quick brown rabbit", metadata={"tag": "b"}),
        Row(id="r4", text="delta slow turtle", metadata={"tag": "b"}),
        Row(id="r5", text="epsilon swift hare quick", metadata={"tag": "c"}),
        Row(id="r6", text="zeta brown bear", metadata={"tag": "c"}),
    ]


async def test_hybrid_is_rrf_fusion_of_vector_and_fts(tmp_path: Path) -> None:
    """``mode='hybrid'`` returns the RRF fusion of vector + fts branches."""
    store = LanceDBVectorStore(tmp_path / "vectors", FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()
    await store.upsert(_seed_rows())

    embedder = FakeEmbedder(ndims=_NDIMS)
    query_text = "alpha quick brown"
    query_vec = (await embedder.embed([query_text], kind="query"))[0]

    vector_hits = await store.search(vector=query_vec, k=_K, mode="vector")
    fts_hits = await store.search(text=query_text, k=_K, mode="fts")
    hybrid_hits = await store.search(
        vector=query_vec,
        text=query_text,
        k=_K,
        mode="hybrid",
    )

    # Both branches must contribute (otherwise the test is degenerate).
    assert vector_hits, "mode='vector' returned no hits"
    assert fts_hits, "mode='fts' returned no hits"
    assert hybrid_hits, "mode='hybrid' returned no hits"

    vector_ids = {h.id for h in vector_hits}
    fts_ids = {h.id for h in fts_hits}
    hybrid_ids = {h.id for h in hybrid_hits}

    # Hybrid set is a subset of (vector union fts) and includes elements from each.
    union_ids = vector_ids | fts_ids
    assert hybrid_ids <= union_ids, (
        f"hybrid={sorted(hybrid_ids)} not subset of union={sorted(union_ids)}"
    )
    assert hybrid_ids & vector_ids, "hybrid must include at least one vector hit"
    assert hybrid_ids & fts_ids, "hybrid must include at least one fts hit"

    # Exact fusion contract: hybrid order == RRFReranker([vector, fts]).
    expected = await RRFReranker().fuse([vector_hits, fts_hits], k=_K)
    assert [h.id for h in hybrid_hits] == [h.id for h in expected]
    for got, want in zip(hybrid_hits, expected, strict=True):
        assert abs(got.score - want.score) < 1e-9
