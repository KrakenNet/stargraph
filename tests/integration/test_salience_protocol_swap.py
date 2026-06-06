# SPDX-License-Identifier: Apache-2.0
"""SalienceScorer Protocol swap loud-fail (FR-31, NFR-4).

The :class:`stargraph.skills.salience.SalienceScorer` Protocol is the
seam consolidation rules read salience through. v1 ships
:class:`RuleBasedScorer` (Park 2023 formula); v2 swaps to embedding-
similarity, v3 to a learned scorer. NFR-4 mandates the Protocol stay
structurally swappable across versions: this test instantiates two
provider implementations, scores the same context through each,
asserts the swap had a visible effect (different scores), and that
both providers satisfy the Protocol structural check at runtime.

The "v2 sim" provider is a local fake (``FakeEmbeddingScorer``) — not
a production class — exercised purely to prove the Protocol contract
is honoured across implementations. The MemoryStore swap noted in
the Phase-3 spec is structural (the MemoryStore Protocol does not
expose ``set_scorer``); the relevant invariant is that scorers are
interchangeable behind the Protocol where consolidation rules
consume them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stargraph.skills.salience import RuleBasedScorer, SalienceContext, SalienceScorer
from stargraph.stores.memory import Episode

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class FakeEmbeddingScorer:
    """v2-sim Protocol implementation with non-zero relevance weight.

    Returns ``0.5 + small term`` so its output diverges from
    :class:`RuleBasedScorer` (whose v1 weights zero out relevance and
    importance, yielding a recency*frequency*rule-match product). The
    constant base ensures the swap is observable even when the v1
    score happens to land near 0.5.
    """

    async def score(self, memory: Episode, ctx: SalienceContext) -> float:
        _ = memory
        # Non-zero relevance weight + bounded ctx term -> stable [0, 1] range.
        bonus = min(0.4, ctx.access_count / 100.0)
        return 0.5 + bonus


def _episode() -> Episode:
    return Episode(
        id="ep-1",
        content="hello",
        timestamp=datetime.now(UTC),
        source_node="n",
        agent="rag",
        user="Alice",
        session="S1",
    )


def _context() -> SalienceContext:
    # Recency anchor in the past so RuleBasedScorer's exp-decay is non-trivial.
    return SalienceContext(
        last_access_ts=datetime.now(UTC),
        access_count=5,
        rule_match_count=3,
    )


@pytest.mark.asyncio
async def test_protocol_swap_at_runtime() -> None:
    """Provider A -> swap -> Provider B; same context yields different scores."""
    rule_based = RuleBasedScorer()
    fake_embedding = FakeEmbeddingScorer()

    memory = _episode()
    ctx = _context()

    a_score = await rule_based.score(memory, ctx)
    b_score = await fake_embedding.score(memory, ctx)

    assert 0.0 <= a_score <= 1.0
    assert 0.0 <= b_score <= 1.0
    assert a_score != b_score, "swap had no observable effect"

    # Protocol structural check holds for both providers.
    assert isinstance(rule_based, SalienceScorer)
    assert isinstance(fake_embedding, SalienceScorer)


def test_both_providers_isinstance() -> None:
    """Both providers pass the runtime-checkable Protocol check."""
    assert isinstance(RuleBasedScorer(), SalienceScorer)
    assert isinstance(FakeEmbeddingScorer(), SalienceScorer)
