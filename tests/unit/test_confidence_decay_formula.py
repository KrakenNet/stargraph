# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the last-write confidence-decay formula (FR-11, Learning B).

Per design §3.6.3 the documented formula is::

    c_merged = c_winner * (1 - 0.5 * (n - 1) / n)

with concrete pinned values at n=2, n=3, n=4.  This file pins those
constants so any future tweak to the decay curve must be intentional and
re-anchored against design.

This is the **TDD-RED** half of the FR-11 cycle.  The
``stargraph.runtime.merge`` module does not yet exist; imports are deferred
inside each test body so the file parses cleanly under ruff/pyright while
the tests themselves fail with :class:`ImportError` until task 3.11 lands
the implementation.
"""

from __future__ import annotations

import importlib
import math
from itertools import pairwise
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:

    class _Decay(Protocol):
        def __call__(self, *, c_winner: float, n: int) -> float: ...

    class _MergeModule(Protocol):
        confidence_after_last_write_conflict: _Decay


def _decay() -> _Decay:
    """Deferred import of the not-yet-built decay function."""
    module = cast("_MergeModule", importlib.import_module("stargraph.runtime.merge"))
    return module.confidence_after_last_write_conflict


def test_decay_n2_drops_to_three_quarters() -> None:
    """n=2 ⇒ multiplier 0.75 (-25%) per design §3.6.3."""
    assert math.isclose(_decay()(c_winner=1.0, n=2), 0.75)


def test_decay_n3_drops_to_two_thirds() -> None:
    """n=3 ⇒ multiplier 2/3 (-33%) per design §3.6.3."""
    assert math.isclose(_decay()(c_winner=1.0, n=3), 2.0 / 3.0)


def test_decay_n4_drops_to_five_eighths() -> None:
    """n=4 ⇒ multiplier 0.625 (-37.5%) per design §3.6.3."""
    assert math.isclose(_decay()(c_winner=1.0, n=4), 0.625)


def test_decay_scales_linearly_in_winner_confidence() -> None:
    """Formula is linear in ``c_winner``; halving the input halves the output."""
    fn = _decay()
    full = fn(c_winner=0.8, n=3)
    half = fn(c_winner=0.4, n=3)
    assert math.isclose(full, 2.0 * half)


def test_decay_n1_is_identity() -> None:
    """A single branch is not a conflict; multiplier collapses to 1.0."""
    assert math.isclose(_decay()(c_winner=0.9, n=1), 0.9)


def test_decay_monotonic_in_n() -> None:
    """More competing branches must lower confidence further (or equal)."""
    fn = _decay()
    series = [fn(c_winner=1.0, n=n) for n in range(1, 10)]
    for prev, nxt in pairwise(series):
        assert nxt <= prev or math.isclose(nxt, prev)
