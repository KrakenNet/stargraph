# SPDX-License-Identifier: Apache-2.0
"""Protocol shape pin for :class:`SalienceScorer` (FR-31).

Salience scoring swaps implementations across the v1 → v2 → v3 path
(rule-based → embedding-similarity → learned). The Protocol must stay
``@runtime_checkable`` so dependency-injection sites can validate
substitutes via :func:`isinstance` without importing concrete classes.
"""

from __future__ import annotations

import pytest

from stargraph.skills.salience import RuleBasedScorer, SalienceScorer

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_salience_scorer_is_runtime_protocol() -> None:
    """``SalienceScorer`` carries the runtime-checkable Protocol marker."""
    assert getattr(SalienceScorer, "_is_runtime_protocol", False) is True


def test_rule_based_scorer_satisfies_protocol() -> None:
    """``RuleBasedScorer()`` is recognized as a :class:`SalienceScorer`."""
    assert isinstance(RuleBasedScorer(), SalienceScorer)
