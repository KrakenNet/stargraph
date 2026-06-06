# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``bosun/budgets/__init__.py`` rule logic (T12).

Pins the sibling-shape contract (mirrors ``bosun/safety_pii/__init__.py``):
``decide()`` is a sync function returning a :class:`BudgetDecision` derived
from ``_PATTERNS`` walking.
"""

from __future__ import annotations

import pytest

from stargraph.bosun.budgets import BudgetDecision, decide

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_decide_returns_budget_decision_for_known_pattern() -> None:
    """A matching ``_PATTERNS`` entry yields a :class:`BudgetDecision` (T12)."""
    result = decide(budget_kind="tokens", used=900.0, limit=1000.0)
    assert isinstance(result, BudgetDecision)
    assert result.action in {"allow", "throttle", "deny"}
    assert result.reason != ""


@pytest.mark.unit
def test_decide_returns_no_action_when_under_budget() -> None:
    """Plenty-remaining budget yields ``action="allow"`` (T12)."""
    result = decide(budget_kind="tokens", used=1.0, limit=1000.0)
    assert isinstance(result, BudgetDecision)
    assert result.action == "allow"
