# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :class:`harbor.stores.rerankers.CrossEncoderReranker`.

Resolves TODO #17 (Findings from docs build, 2026-05-04): the reranker
is no longer a NotImplementedError stub — it ships a real
sentence-transformers ``CrossEncoder``-backed implementation behind
the ``skills-rag`` extra and is registered under the
``harbor.rerankers`` entry-point group as ``cross-encoder``.

Tests use a fake ``_encoder`` so the assertions do not depend on
loading a real model (saves ~150MB of weights + several seconds per
session). The lazy-load path is exercised separately by mocking the
``sentence_transformers`` import.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from harbor.errors import HarborRuntimeError
from harbor.stores.rerankers import CrossEncoderReranker, Reranker
from harbor.stores.vector import Hit

pytestmark = [pytest.mark.unit, pytest.mark.knowledge]


def _make_reranker_with_fake_encoder(
    scores_by_pair: dict[tuple[str, str], float] | None = None,
) -> tuple[CrossEncoderReranker, list[list[tuple[str, str]]]]:
    """Build a reranker with the encoder pre-stubbed.

    Returns ``(reranker, captured_pairs)`` so tests can assert the
    exact (query, doc) pairs passed to ``encoder.predict``.
    """
    captured: list[list[tuple[str, str]]] = []

    def _predict(
        pairs: list[tuple[str, str]],
        *,
        batch_size: int = 32,
    ) -> list[float]:
        del batch_size
        captured.append(list(pairs))
        if scores_by_pair is None:
            return [float(len(pair[1])) for pair in pairs]  # longer doc = higher score
        return [scores_by_pair.get(pair, 0.0) for pair in pairs]

    reranker = CrossEncoderReranker()
    reranker._encoder = MagicMock()  # pyright: ignore[reportPrivateUsage]
    reranker._encoder.predict = _predict  # pyright: ignore[reportPrivateUsage]
    return reranker, captured


async def test_cross_encoder_satisfies_protocol() -> None:
    """``CrossEncoderReranker`` is a structural :class:`Reranker`."""
    rr = CrossEncoderReranker()
    assert isinstance(rr, Reranker)


async def test_cross_encoder_scores_and_ranks_top_k() -> None:
    """Hits are sorted by encoder score desc, truncated to k."""
    rr, captured = _make_reranker_with_fake_encoder(
        scores_by_pair={
            ("query", "alpha doc"): 0.1,
            ("query", "beta doc longest"): 0.9,
            ("query", "gamma"): 0.5,
        },
    )
    hits = [
        [
            Hit(id="a", score=1.0, metadata={"text": "alpha doc"}),
            Hit(id="b", score=0.9, metadata={"text": "beta doc longest"}),
            Hit(id="c", score=0.8, metadata={"text": "gamma"}),
        ],
    ]

    out = await rr.fuse(hits, k=2, query="query")

    assert [h.id for h in out] == ["b", "c"]
    assert out[0].score == pytest.approx(0.9)  # pyright: ignore[reportUnknownMemberType]
    assert captured[0] == [
        ("query", "alpha doc"),
        ("query", "beta doc longest"),
        ("query", "gamma"),
    ]


async def test_cross_encoder_dedupes_across_per_store_lists() -> None:
    """Hit appearing in multiple stores is scored once; first-seen metadata wins."""
    rr, captured = _make_reranker_with_fake_encoder(
        scores_by_pair={("q", "first metadata"): 0.7},
    )
    hits = [
        [Hit(id="x", score=1.0, metadata={"text": "first metadata"})],
        [Hit(id="x", score=0.5, metadata={"text": "second metadata"})],
    ]

    out = await rr.fuse(hits, k=5, query="q")

    assert len(out) == 1
    assert out[0].id == "x"
    assert out[0].metadata == {"text": "first metadata"}
    # Encoder saw only the first-seen text, not the duplicate.
    assert captured[0] == [("q", "first metadata")]


async def test_cross_encoder_falls_back_to_hit_id_when_no_text() -> None:
    """Missing metadata text → hit.id passes through as the encoder doc."""
    rr, captured = _make_reranker_with_fake_encoder()
    hits = [
        [Hit(id="bare-id", score=1.0, metadata={})],
    ]

    await rr.fuse(hits, k=1, query="q")

    assert captured[0] == [("q", "bare-id")]


async def test_cross_encoder_searches_metadata_keys_in_order() -> None:
    """``content`` is consulted when ``text`` is absent."""
    rr, captured = _make_reranker_with_fake_encoder()
    hits = [
        [Hit(id="h", score=1.0, metadata={"content": "from-content"})],
    ]

    await rr.fuse(hits, k=1, query="q")

    assert captured[0] == [("q", "from-content")]


async def test_cross_encoder_raises_loudly_when_query_missing() -> None:
    """``query=None`` is a wiring bug -- raise rather than degrade silently."""
    rr, _ = _make_reranker_with_fake_encoder()
    hits = [[Hit(id="a", score=1.0, metadata={"text": "doc"})]]

    with pytest.raises(HarborRuntimeError, match="requires a non-empty 'query'"):
        await rr.fuse(hits, k=1, query=None)
    with pytest.raises(HarborRuntimeError, match="requires a non-empty 'query'"):
        await rr.fuse(hits, k=1, query="")


async def test_cross_encoder_returns_empty_on_empty_input() -> None:
    """No hits -> empty out, no encoder call."""
    rr, captured = _make_reranker_with_fake_encoder()
    out = await rr.fuse([], k=5, query="q")
    assert out == []
    assert captured == []


def test_cross_encoder_lazy_import_raises_when_extras_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing ``sentence_transformers`` import surfaces a clear hint."""
    rr = CrossEncoderReranker()
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(
        HarborRuntimeError,
        match=r"requires the 'skills-rag' extra",
    ):
        rr._load_encoder()  # pyright: ignore[reportPrivateUsage]


def test_cross_encoder_registered_under_rerankers_entry_point_group() -> None:
    """``harbor.rerankers:cross-encoder`` resolves to the class."""
    from importlib.metadata import entry_points

    matches = [ep for ep in entry_points(group="harbor.rerankers") if ep.name == "cross-encoder"]
    assert matches, "cross-encoder entry point not registered under harbor.rerankers"
    cls: Any = matches[0].load()
    assert cls is CrossEncoderReranker
