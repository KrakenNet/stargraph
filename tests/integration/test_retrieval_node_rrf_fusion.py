# SPDX-License-Identifier: Apache-2.0
"""RetrievalNode RRF fusion contract test (FR-26, AC-4, Task 3.30).

Pins the fused-rank order produced by
:class:`stargraph.nodes.retrieval.RetrievalNode` against the canonical RRF
formula ``score(id) = Σ_lists 1/(k_param + rank_in_list)`` (rank is
1-based).

We use stub stores so each branch returns a hand-crafted hit list with
overlapping ids at different ranks; the assertion compares the fused
output to the manually computed RRF order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from stargraph.ir._models import StoreRef
from stargraph.nodes.retrieval import RetrievalNode
from stargraph.stores.rerankers import RRFReranker
from stargraph.stores.vector import Hit

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class _RetrievalState(BaseModel):
    query: str


class _Ctx:
    run_id: str = "rrf-fusion-test"


class _StubVectorStore:
    """In-memory stub returning a hard-coded hit list for ``search``."""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits

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
        del vector, text, filter
        return list(self._hits)

    async def delete(self, ids: list[str]) -> int:  # pragma: no cover -- not exercised
        del ids
        return 0


def _hit(hit_id: str, score: float = 0.0) -> Hit:
    return Hit(id=hit_id, score=score, metadata={})


async def test_fused_order_matches_rrf_formula() -> None:
    """Fused output ranks ids by manually computed Σ 1/(k_param + rank).

    Two stub stores. Ids ``A``, ``B``, ``C``, ``D`` appear at varied
    ranks across the lists, and ``A`` shows up in BOTH stores (so its
    score sums two contributions). The fused order must match the
    formula's ranking (descending by sum-of-reciprocals).
    """
    store_a_hits = [_hit("A"), _hit("B"), _hit("C")]  # ranks 1, 2, 3
    store_b_hits = [_hit("D"), _hit("A"), _hit("E")]  # ranks 1, 2, 3

    store_a = _StubVectorStore(store_a_hits)
    store_b = _StubVectorStore(store_b_hits)

    def _resolver(name: str) -> VectorStore | DocStore:
        if name == "a":
            return cast("VectorStore", store_a)
        if name == "b":
            return cast("VectorStore", store_b)
        raise KeyError(name)

    node = RetrievalNode(
        stores=[
            StoreRef(name="a", provider="lancedb"),
            StoreRef(name="b", provider="lancedb"),
        ],
        store_resolver=_resolver,
        k=10,
    )

    out = await node.execute(
        _RetrievalState(query="x"),
        cast("ExecutionContext", _Ctx()),
    )
    fused: list[Hit] = out["retrieved"]

    # Hand-compute the expected ordering using k_param = RRFReranker default.
    k_param = RRFReranker().k_param  # 60
    expected_scores: dict[str, float] = {}
    for hit_list in (store_a_hits, store_b_hits):
        for rank, hit in enumerate(hit_list, start=1):
            expected_scores[hit.id] = expected_scores.get(hit.id, 0.0) + 1.0 / (k_param + rank)
    expected_order = [
        hid for hid, _ in sorted(expected_scores.items(), key=lambda kv: kv[1], reverse=True)
    ]

    actual_order = [h.id for h in fused]
    assert actual_order == expected_order

    # ``A`` appears in both lists at rank 1 + rank 2 → must outrank
    # singleton-list ids ``D`` (rank 1 in B) and ``B`` (rank 2 in A).
    assert actual_order[0] == "A"

    # Score on the fused hit equals the formula sum (sanity peg).
    fused_a = next(h for h in fused if h.id == "A")
    expected_a = 1.0 / (k_param + 1) + 1.0 / (k_param + 2)
    assert abs(fused_a.score - expected_a) < 1e-12
