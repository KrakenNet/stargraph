# SPDX-License-Identifier: Apache-2.0
"""Property tests for :class:`RRFReranker` fusion stability (FR-16).

Reciprocal Rank Fusion is the model-free default reranker behind
``RetrievalNode`` (design §3.8). Two algebraic invariants matter for
correctness:

1. **Permutation invariance** -- fusion is a sum over per-store ranks
   (``Σ_lists 1/(k + rank)``), so the order in which the per-store
   lists are presented must not change the fused result. Ties are
   broken deterministically by ``id`` so the output is fully
   permutation-stable, not merely score-stable.
2. **Idempotency on a single list** -- if the input already has one
   list, re-fusing the output (still a single list) must reproduce the
   same ordering. Concretely, ``fuse([fuse([hits])])`` is sorted by
   ``1/(k + rank)`` which is monotone in ``rank``, so the rank-1 hit
   stays at rank 1, rank-2 at rank 2, and so on.

Hypothesis generates per-store hit-lists with unique ids per list and
varying lengths; both invariants are checked across 50 examples.
"""

from __future__ import annotations

import asyncio
import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stargraph.stores.rerankers import RRFReranker
from stargraph.stores.vector import Hit

pytestmark = [pytest.mark.knowledge, pytest.mark.property]


_ID_ALPHABET = string.ascii_lowercase + string.digits


def _hit(hit_id: str) -> Hit:
    return Hit(id=hit_id, score=0.0, metadata={})


@st.composite
def _hit_list(draw: st.DrawFn) -> list[Hit]:
    """Generate a per-store ranked list with unique ids."""

    ids = draw(
        st.lists(
            st.text(alphabet=_ID_ALPHABET, min_size=1, max_size=4),
            min_size=1,
            max_size=8,
            unique=True,
        )
    )
    return [_hit(hit_id) for hit_id in ids]


@st.composite
def _per_store(draw: st.DrawFn) -> list[list[Hit]]:
    return draw(st.lists(_hit_list(), min_size=1, max_size=4))


def _sort_key(hit: Hit) -> tuple[float, str]:
    """Order by score desc (negate), then id asc -- deterministic ties."""

    return (-hit.score, hit.id)


def _normalise(hits: list[Hit]) -> list[tuple[str, float]]:
    return [(h.id, h.score) for h in sorted(hits, key=_sort_key)]


@settings(max_examples=50, deadline=None)
@given(per_store=_per_store(), seed=st.integers(min_value=0, max_value=2**16))
def test_rrf_permutation_invariant(per_store: list[list[Hit]], seed: int) -> None:
    """Fusing a permutation of per-store lists yields the same output."""

    # Hypothesis-derived deterministic permutation: rotate by ``seed`` mod n.
    n = len(per_store)
    offset = seed % n
    permuted = per_store[offset:] + per_store[:offset]

    fuser = RRFReranker()
    k = 50
    fused_a = asyncio.run(fuser.fuse(per_store, k=k))
    fused_b = asyncio.run(fuser.fuse(permuted, k=k))

    assert _normalise(fused_a) == _normalise(fused_b)


@settings(max_examples=50, deadline=None)
@given(hits=_hit_list())
def test_rrf_idempotent_on_single_list(hits: list[Hit]) -> None:
    """fuse(fuse([hits])) preserves the order produced by fuse([hits])."""

    fuser = RRFReranker()
    k = 50
    once = asyncio.run(fuser.fuse([hits], k=k))
    twice = asyncio.run(fuser.fuse([once], k=k))

    # The id ordering is identical; scores will recompute against the new
    # ranks so we compare ids only (the algebraic fixed-point is on order,
    # not on the score values).
    assert [h.id for h in once] == [h.id for h in twice]
