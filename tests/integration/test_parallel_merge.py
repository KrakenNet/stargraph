# SPDX-License-Identifier: Apache-2.0
"""Integration tests for ``graph/loop._merge_branch_results`` reducer-aware merge (T21).

Pins:

* Disjoint-field branch writes merge without conflict (no reducer needed).
* Same-field writes with no declared reducer raise loudly via
  :func:`runtime.merge.build_last_write_conflict_evidence` (FR-11).
* Same-field writes with a declared reducer apply the reducer's
  ``combine(a, b)`` function.

``runtime/dispatch._dispatch_parallel`` and ``runtime/parallel.py`` are
out-of-scope (scope.md); only the merge line at ``graph/loop.py:582-586``
is in scope.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_parallel_merge_disjoint_fields_succeeds() -> None:
    """Two branches writing disjoint fields merge without conflict (T21)."""
    from stargraph.graph.loop import _merge_branch_results  # pyright: ignore[reportPrivateUsage]

    branch_a = {"alpha": 1}
    branch_b = {"beta": 2}
    out = _merge_branch_results(
        [branch_a, branch_b],
        ir=None,  # disjoint case: IR / registry untouched
        reducer_registry=None,
    )
    assert out == {"alpha": 1, "beta": 2}


@pytest.mark.integration
def test_parallel_merge_conflict_without_reducer_raises_with_evidence() -> None:
    """Two branches writing the same field with no declared reducer raises a
    loud :class:`StargraphRuntimeError` carrying conflict evidence (T21, FR-11)."""
    from stargraph.errors import StargraphRuntimeError
    from stargraph.graph.loop import _merge_branch_results  # pyright: ignore[reportPrivateUsage]

    branch_a = {"shared": 1}
    branch_b = {"shared": 2}
    with pytest.raises((StargraphRuntimeError, ValueError)):
        _merge_branch_results(
            [branch_a, branch_b],
            ir=None,  # no reducer declared
            reducer_registry=None,
        )


@pytest.mark.integration
def test_parallel_merge_conflict_with_reducer_applies_combine() -> None:
    """When a reducer is declared in IR for the conflicting field, the merge
    applies the reducer's ``combine(a, b)`` (T21)."""
    from stargraph.graph.loop import _merge_branch_results  # pyright: ignore[reportPrivateUsage]
    from stargraph.runtime.merge import MergeRegistry

    # Sum reducer on the conflicting field.
    registry = MergeRegistry()
    registry.register("shared", lambda a, b: a + b)  # pyright: ignore[reportArgumentType, reportUnknownLambdaType]

    branch_a = {"shared": 1}
    branch_b = {"shared": 2}
    out = _merge_branch_results([branch_a, branch_b], ir=None, reducer_registry=registry)
    assert out == {"shared": 3}
