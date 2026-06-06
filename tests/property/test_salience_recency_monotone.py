# SPDX-License-Identifier: Apache-2.0
"""Property test: salience is monotone non-decreasing in ``last_access_ts`` (FR-31).

Park 2023 §4.1 ranks memories by recency: a more recently accessed
episode must score at least as high as an older one when all other
signals are fixed. This pins that monotonicity on the v1
:class:`RuleBasedScorer` so the consolidation gate (AC-5.5) cannot
silently invert ordering when the decay term changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stargraph.skills.salience import RuleBasedScorer, SalienceContext
from stargraph.stores.memory import Episode

pytestmark = [pytest.mark.knowledge, pytest.mark.property]


def _episode() -> Episode:
    return Episode(
        id="ep-monotone",
        content="probe",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source_node="n",
        agent="rag",
        user="Alice",
        session="S1",
    )


@st.composite
def _twin_contexts(draw: st.DrawFn) -> tuple[SalienceContext, SalienceContext]:
    """Build two contexts that differ ONLY in ``last_access_ts``.

    ``older`` anchors at ``base``; ``newer`` anchors at ``base + delta`` for
    ``delta`` strictly positive (≥1 s) so the comparison is unambiguous
    even though :meth:`RuleBasedScorer.score` reads :func:`datetime.now`
    independently per call.
    """
    base_offset_seconds = draw(st.integers(min_value=1, max_value=10_000_000))
    delta_seconds = draw(st.integers(min_value=1, max_value=1_000_000))
    now = datetime.now(UTC)
    older_ts = now - timedelta(seconds=base_offset_seconds + delta_seconds)
    newer_ts = now - timedelta(seconds=base_offset_seconds)

    access_count = draw(st.integers(min_value=0, max_value=1_000))
    rule_match_count = draw(st.integers(min_value=0, max_value=1_000))
    decay_tau_seconds = draw(st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False))
    weights = {
        "recency": draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        "relevance": draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        "importance": draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
    }

    older = SalienceContext(
        query_embedding=None,
        last_access_ts=older_ts,
        access_count=access_count,
        rule_match_count=rule_match_count,
        weights=weights,
        decay_tau_seconds=decay_tau_seconds,
    )
    newer = SalienceContext(
        query_embedding=None,
        last_access_ts=newer_ts,
        access_count=access_count,
        rule_match_count=rule_match_count,
        weights=dict(weights),
        decay_tau_seconds=decay_tau_seconds,
    )
    return older, newer


@settings(max_examples=50, deadline=None)
@given(twin=_twin_contexts())
async def test_recency_monotone_non_decreasing(
    twin: tuple[SalienceContext, SalienceContext],
) -> None:
    """``score(older) <= score(newer)`` when all other fields match."""
    older, newer = twin
    scorer = RuleBasedScorer()
    episode = _episode()
    score_older = await scorer.score(episode, older)
    score_newer = await scorer.score(episode, newer)
    assert score_older <= score_newer
