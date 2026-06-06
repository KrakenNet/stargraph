# SPDX-License-Identifier: Apache-2.0
"""Property tests for merge-strategy associativity (FR-11).

Per requirements FR-11 the four built-in merge strategies map onto the
LangGraph channel-reducer ontology:

* ``last-write`` ↔ ``LastValue`` — *non*-associative when paired with the
  lowered-confidence wrapper from design §3.6.3 (the two-branch vs
  three-branch formulas yield distinct results, so left-fold and direct
  fold disagree — captured here as a regression pin).
* ``append`` ↔ ``Topic``               — associative (list concatenation).
* ``max`` / ``min`` ↔ ``BinaryOperatorAggregate`` — associative
  (commutative monoid over total orders).

This is the **TDD-RED** half of the FR-11 cycle.  The
``stargraph.runtime.merge`` module does not yet exist; imports are deferred
inside each test body so the file parses cleanly under ruff/pyright while
the tests themselves fail with :class:`ImportError` until task 3.11 lands
the implementation.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Protocol, cast

from hypothesis import given
from hypothesis import strategies as st

if TYPE_CHECKING:

    class _Strategy(Protocol):
        def __call__(self, values: list[Any]) -> Any: ...

    class _MergeRegistry(Protocol):
        def get(self, name: str) -> _Strategy: ...

    class _Decay(Protocol):
        def __call__(self, *, c_winner: float, n: int) -> float: ...

    class _MergeModule(Protocol):
        MergeRegistry: type[_MergeRegistry]
        confidence_after_last_write_conflict: _Decay


def _module() -> _MergeModule:
    return cast("_MergeModule", importlib.import_module("stargraph.runtime.merge"))


@given(
    a=st.lists(st.integers(), min_size=1, max_size=4),
    b=st.lists(st.integers(), min_size=1, max_size=4),
    c=st.lists(st.integers(), min_size=1, max_size=4),
)
def test_append_is_associative(a: list[int], b: list[int], c: list[int]) -> None:
    """``append`` is list-concat; ``(a++b)++c == a++(b++c)``."""
    append = _module().MergeRegistry().get("append")
    left = append([append([a, b]), c])
    right = append([a, append([b, c])])
    assert left == right


@given(xs=st.lists(st.integers(), min_size=3, max_size=12))
def test_max_is_associative(xs: list[int]) -> None:
    """``max`` is associative: any partition gives the same overall maximum."""
    mx = _module().MergeRegistry().get("max")
    mid = len(xs) // 2
    left_first = mx([mx(xs[:mid]), mx(xs[mid:])])
    right_first = mx([xs[0], mx(xs[1:])])
    assert left_first == right_first == mx(xs)


@given(xs=st.lists(st.integers(), min_size=3, max_size=12))
def test_min_is_associative(xs: list[int]) -> None:
    """``min`` is associative: any partition gives the same overall minimum."""
    mn = _module().MergeRegistry().get("min")
    mid = len(xs) // 2
    left_first = mn([mn(xs[:mid]), mn(xs[mid:])])
    right_first = mn([xs[0], mn(xs[1:])])
    assert left_first == right_first == mn(xs)


def test_last_write_confidence_decay_is_not_associative() -> None:
    """Per design §3.6.3 the lowered-confidence formula is non-associative.

    Resolving three branches in one shot uses ``n=3`` (multiplier 2/3).
    Resolving them as ``merge(merge(b1, b2), b3)`` applies ``n=2`` twice
    (multiplier 0.75 * 0.75 = 0.5625).  Those are unequal, so left-fold
    and direct fold disagree — the canonical FR-11 non-associativity
    witness, captured here as a regression pin.
    """
    fn = _module().confidence_after_last_write_conflict
    direct = fn(c_winner=1.0, n=3)
    folded = fn(c_winner=fn(c_winner=1.0, n=2), n=2)
    assert direct != folded
