# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``bosun/retries/__init__.py`` rule logic (T11).

Pins the sibling-shape contract (mirrors ``bosun/safety_pii/__init__.py``):
``decide()`` is a sync function returning a :class:`RetryDecision` derived
from ``_PATTERNS`` walking.
"""

from __future__ import annotations

import pytest

from harbor.bosun.retries import RetryDecision, decide

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_decide_returns_retry_decision_for_known_pattern() -> None:
    """A matching ``_PATTERNS`` entry yields a :class:`RetryDecision` with
    ``should_retry=True`` and a non-empty ``reason`` (T11)."""
    result = decide(error="TransientNetworkError", attempt=1, max_attempts=3)
    assert isinstance(result, RetryDecision)
    assert result.should_retry is True
    assert result.reason != ""


@pytest.mark.unit
def test_decide_returns_no_retry_when_no_pattern_matches() -> None:
    """An unknown error class falls through to ``should_retry=False`` (T11)."""
    result = decide(error="UnclassifiedError", attempt=99, max_attempts=3)
    assert isinstance(result, RetryDecision)
    assert result.should_retry is False
    assert result.reason == "no_pattern_matched"
