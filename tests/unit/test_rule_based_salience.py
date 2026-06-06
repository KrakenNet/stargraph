# SPDX-License-Identifier: Apache-2.0
"""Snapshot test: :class:`RuleBasedScorer` numerical output (FR-31, AC-5.5).

Pins the Park 2023 formula constants on a fixed input so refactors of
:mod:`stargraph.skills.salience` cannot silently shift the distribution
that downstream consolidation thresholds were tuned against. The
recency anchor is mocked via :func:`datetime.now` so the assertion is
deterministic.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from stargraph.skills.salience import RuleBasedScorer, SalienceContext
from stargraph.stores.memory import Episode

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


_FAKE_NOW = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
_LAST_ACCESS = datetime(2026, 4, 29, 11, 0, 0, tzinfo=UTC)  # exactly 1h earlier


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        del tz
        return _FAKE_NOW


async def test_rule_based_scorer_snapshot() -> None:
    """Fixed inputs → known output: 1h delta, tau=1d, access=20, rule=15."""
    episode = Episode(
        id="ep-snap",
        content="snapshot probe",
        timestamp=_LAST_ACCESS,
        source_node="n",
        agent="rag",
        user="Alice",
        session="S1",
    )
    ctx = SalienceContext(
        query_embedding=None,
        last_access_ts=_LAST_ACCESS,
        access_count=20,
        rule_match_count=15,
        decay_tau_seconds=86400.0,
    )
    # Default weights: recency=1.0, relevance=0.0, importance=0.0.
    expected_recency = math.exp(-3600.0 / 86400.0)
    expected_freq = math.tanh(20 / 10.0)
    expected_rule = math.tanh(15 / 5.0)
    expected = expected_recency * expected_freq * expected_rule

    with patch("stargraph.skills.salience.datetime", _FrozenDatetime):
        score = await RuleBasedScorer().score(episode, ctx)

    assert math.isclose(score, expected, rel_tol=1e-9)
    # Spot-check the absolute value so a refactor of the formula trips even
    # if the recomputed `expected` accidentally tracks the regression.
    assert math.isclose(score, 0.9201122956331096, rel_tol=1e-9)
