# SPDX-License-Identifier: Apache-2.0
"""Property test: ``RuleBasedScorer().score(...)`` ∈ [0, 1] (FR-31, AC-5.5).

Park 2023 §4.1 keeps salience in the unit interval so callers can apply a
single threshold gate before episodic→semantic consolidation. This file
pins the bound on the v1 :class:`RuleBasedScorer` across Hypothesis-generated
:class:`SalienceContext` shapes -- weights, decay tau, access counts, and
``last_access_ts`` all vary; the clamp in :meth:`RuleBasedScorer.score`
must hold regardless.
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
        id="ep-bounds",
        content="probe",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source_node="n",
        agent="rag",
        user="Alice",
        session="S1",
    )


# Anchor strictly in the past (the only realistic shape: an episode is
# accessed BEFORE it is scored). 0 .. 10 years covers fresh-to-stale.
_TEN_YEARS = int(timedelta(days=3650).total_seconds())


@st.composite
def _salience_contexts(draw: st.DrawFn) -> SalienceContext:
    delta_seconds = draw(st.integers(min_value=0, max_value=_TEN_YEARS))
    last_access_ts = datetime.now(UTC) - timedelta(seconds=delta_seconds)
    weights = {
        "recency": draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        "relevance": draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        "importance": draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
    }
    return SalienceContext(
        query_embedding=None,
        last_access_ts=last_access_ts,
        access_count=draw(st.integers(min_value=0, max_value=10_000)),
        rule_match_count=draw(st.integers(min_value=0, max_value=10_000)),
        weights=weights,
        decay_tau_seconds=draw(st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False)),
    )


@settings(max_examples=50, deadline=None)
@given(ctx=_salience_contexts())
async def test_rule_based_score_in_unit_interval(ctx: SalienceContext) -> None:
    """``RuleBasedScorer().score(...)`` always returns a float in [0.0, 1.0]."""
    score = await RuleBasedScorer().score(_episode(), ctx)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
