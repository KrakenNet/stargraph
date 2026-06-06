# SPDX-License-Identifier: Apache-2.0
"""Integration test for ``CrossEncoderReranker.fuse`` -- T05 top-k contract.

Gated by ``tests/conftest.py:32-75`` ``collect_ignore_glob`` for the
``skills-rag`` optional extra (sentence-transformers + torch).
"""

from __future__ import annotations

import importlib.util

import pytest

from stargraph.stores.rerankers import CrossEncoderReranker
from stargraph.stores.vector import Hit

pytestmark = [pytest.mark.integration, pytest.mark.knowledge]


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="requires the `skills-rag` extra (sentence-transformers)",
)
async def test_cross_encoder_fuse_returns_top_k_sorted_by_score() -> None:
    """``fuse`` returns at most ``k`` hits sorted descending by cross-encoder
    score (stable tiebreak by id) (T05)."""
    rr = CrossEncoderReranker()
    hits = [
        [
            Hit(id="a", score=0.0, metadata={"text": "the cat sat on the mat"}),
            Hit(id="b", score=0.0, metadata={"text": "the dog ran in the park"}),
            Hit(id="c", score=0.0, metadata={"text": "this sentence is unrelated nonsense"}),
        ]
    ]
    out = await rr.fuse(hits, k=2, query="where did the cat sit")
    assert len(out) == 2
    # Top result should be the cat sentence (semantically closest).
    assert out[0].id == "a"
