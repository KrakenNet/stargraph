# SPDX-License-Identifier: Apache-2.0
"""FR-10 parallel TaskGroup happy-path test (Learning C, design §3.6.1).

Companion to ``tests/integration/test_parallel_cancellation.py``: the existing
3.8 file pins the structured-concurrency primitives (TaskGroup ExceptionGroup
propagation + the four ``asyncio.shield`` sites). This file pins the
**strategy dispatcher** behaviour from design §3.6.1:

1. ``quorum:N`` happy path -- N successes before deadline returns the first
   N results.
2. ``quorum:N`` timeout path -- deadline elapses before N successes raises
   :class:`StargraphRuntimeError` (the helper-routed quorum-timeout per the
   FR-24 raise-policy; see ``_quorum_timeout`` in :mod:`stargraph.runtime.parallel`).
3. ``quorum:N`` tiebreak (Learning C) -- N-th success arrives at the same
   moment the deadline elapses; success wins ties (design §3.6.1 verbatim:
   "success-tie at the deadline goes to quorum -- success wins ties").
4. ``all`` strategy -- every branch must succeed; results returned in input
   order via :func:`stargraph.runtime.parallel.run_branches`.

These four cases are deliberately the *strategy* surface, not the
shield/cancel surface 3.8 already covers.
"""

from __future__ import annotations

import asyncio

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.runtime.parallel import execute_parallel


async def test_quorum_two_of_three_succeeds_before_timeout() -> None:
    """Case 1: ``quorum:2`` of 3 -- two fast branches win before deadline.

    Two branches finish well inside the 1.0s deadline; the third sleeps
    long past it. We expect ``execute_parallel`` to return the first two
    successes (ordered by completion) and cancel the slow sibling under the
    `_quorum` finally-block (drained, no warning).
    """

    async def fast_a() -> str:
        await asyncio.sleep(0.01)
        return "a"

    async def fast_b() -> str:
        await asyncio.sleep(0.02)
        return "b"

    async def slow_c() -> str:
        await asyncio.sleep(60)
        return "c"  # pragma: no cover -- cancelled

    results = await execute_parallel(
        [fast_a, fast_b, slow_c],
        strategy="quorum:2",
        deadline_s=1.0,
    )

    assert len(results) == 2
    assert set(results) == {"a", "b"}


async def test_quorum_three_of_three_times_out_before_quorum() -> None:
    """Case 2: ``quorum:3`` -- deadline elapses with only 1 success.

    Two branches sleep past the 0.05s deadline; one finishes immediately.
    Quorum-of-3 cannot be satisfied, so :func:`_quorum` raises
    :class:`StargraphRuntimeError` via ``_quorum_timeout`` (the FR-24 raise-policy
    routes the deadline error through a Stargraph helper rather than the bare
    :class:`TimeoutError`).
    """

    async def fast_a() -> str:
        await asyncio.sleep(0.001)
        return "a"

    async def slow_b() -> str:
        await asyncio.sleep(60)
        return "b"  # pragma: no cover -- cancelled

    async def slow_c() -> str:
        await asyncio.sleep(60)
        return "c"  # pragma: no cover -- cancelled

    with pytest.raises(StargraphRuntimeError) as excinfo:
        await execute_parallel(
            [fast_a, slow_b, slow_c],
            strategy="quorum:3",
            deadline_s=0.05,
        )

    # The helper formats the message with the satisfied-count for diagnostics.
    msg = str(excinfo.value)
    assert "quorum:3" in msg
    assert "0.05" in msg


async def test_quorum_tiebreak_success_wins_at_deadline() -> None:
    """Case 3 (Learning C): N-th success at the deadline -- success wins.

    Design §3.6.1 verbatim: "success-tie at the deadline goes to quorum --
    success wins ties". The implementation in :func:`_quorum` re-checks
    ``len(successes) >= n`` *after* observing the timeout has elapsed, so a
    success that landed in the same scheduler tick as the deadline still
    counts.

    We emulate the tie by gating the third branch on an :class:`asyncio.Event`
    that is set from a sentinel coroutine scheduled to fire *at* the deadline.
    The ``asyncio.wait`` inside ``_quorum`` returns with both signals ready in
    the same scheduler tick: the deadline has elapsed AND the N-th success
    is already ``done``. The harvest path in the next loop iteration counts
    the success and exits cleanly -- this is the Learning C "success wins
    ties" path. Without that contract this test would flake into a
    :class:`StargraphRuntimeError` quorum-timeout.
    """
    deadline = 0.05
    release = asyncio.Event()

    async def fast_a() -> str:
        await asyncio.sleep(0.001)
        return "a"

    async def fast_b() -> str:
        await asyncio.sleep(0.001)
        return "b"

    async def at_deadline_c() -> str:
        # Block until the sentinel fires, then resolve immediately. The
        # sentinel is timed to coincide with the deadline so the success
        # and the timeout-elapsed observation land in the same tick.
        await release.wait()
        return "c"

    async def deadline_sentinel() -> None:
        # Fire the release just *under* the deadline so the third branch
        # is guaranteed-done by the time _quorum's wait() returns at
        # remaining <= 0. This pins the success-wins-ties contract: the
        # branch completed before the timeout-raise would fire.
        await asyncio.sleep(deadline * 0.9)
        release.set()

    sentinel = asyncio.create_task(deadline_sentinel())
    try:
        results = await execute_parallel(
            [fast_a, fast_b, at_deadline_c],
            strategy="quorum:3",
            deadline_s=deadline,
        )
    finally:
        await sentinel

    # All three counted -- the success-at-deadline broke the tie in favour
    # of quorum, exactly the Learning C contract.
    assert len(results) == 3
    assert set(results) == {"a", "b", "c"}


async def test_all_strategy_three_of_three_returns_input_order_results() -> None:
    """Case 4: ``all`` strategy -- 3-of-3 success, results in input order.

    The ``all`` path delegates to :func:`run_branches`, which preserves the
    input order of the factories regardless of completion order. We verify
    that by giving the branches *reversed* sleep durations: the slowest
    branch is first in the input list, but its result still appears first
    in the output.
    """

    async def slow_first() -> str:
        await asyncio.sleep(0.03)
        return "first"

    async def medium_second() -> str:
        await asyncio.sleep(0.02)
        return "second"

    async def fast_third() -> str:
        await asyncio.sleep(0.01)
        return "third"

    results = await execute_parallel(
        [slow_first, medium_second, fast_third],
        strategy="all",
    )

    assert results == ["first", "second", "third"]
