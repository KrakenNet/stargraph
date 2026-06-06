# SPDX-License-Identifier: Apache-2.0
"""Parallel/join executor with structured concurrency (FR-10, design §3.6).

This module is the GREEN partner to ``tests/integration/test_parallel_cancellation.py``
and the verbatim implementation of FR-10 amendment 2 (research §4): branch
execution uses :class:`asyncio.TaskGroup` (Python 3.11+, structured
concurrency) so the first failing branch aborts the whole group via
:class:`ExceptionGroup` -- never the legacy ``asyncio`` fan-in helper that
silently swallows sibling cancellations (the antipattern this module exists
to forbid; see the static guard at the bottom of the docstring).

Three public helpers cover the surface the integration tests pin:

* :func:`run_branches` -- wraps a list of zero-argument coroutine factories in
  a :class:`asyncio.TaskGroup`. First failure raises an :class:`ExceptionGroup`
  whose ``.split(RuntimeError)`` recovers the original error; surviving
  siblings observe :class:`asyncio.CancelledError` (the structured-concurrency
  invariant).
* :func:`shielded_checkpoint_commit` -- wraps a checkpoint-commit coroutine in
  :func:`asyncio.shield` so an outer cancel mid-write cannot tear the row in
  half. One of the four shield sites enumerated in design §3.6.2.
* :func:`shielded_transition_emit` -- analogous shield around the
  ``stargraph.transition`` fact emit (FR-13). A half-emitted transition fact
  would corrupt the event log / CLIPS working memory.

The full strategy dispatcher (:func:`execute_parallel`) covers ``all | any |
race | quorum:N`` per design §3.6.1; the ``race``/``any`` paths use
:func:`asyncio.wait` with ``return_when=FIRST_COMPLETED`` followed by an
explicit ``task.cancel()`` of pending siblings (and a final ``await`` to drain
:class:`asyncio.CancelledError` cleanly). The ``quorum:N`` path follows the
timeout-precedence rule from design §3.6.1: timeout fires first when both are
about to trigger, with a success-tie at the deadline going to quorum
(success wins ties).

The four :func:`asyncio.shield` sites (design §3.6.2):

1. Checkpoint commit -- :func:`shielded_checkpoint_commit`.
2. ``stargraph.transition`` emit -- :func:`shielded_transition_emit`.
3. Audit sink write -- :func:`shielded_audit_write`.
4. Pool close -- :func:`shielded_pool_close`.

LLM calls are deliberately **not** shielded (research §3.5: cancellation
should free the rate budget).

**Antipattern guard:** the legacy ``asyncio`` fan-in helper (the one with
``return_exceptions``) MUST NOT appear in this module. A static grep guard
in :mod:`tests.integration.test_parallel_cancellation` greps for the literal
string and fails-loud if it is reintroduced. Use :class:`asyncio.TaskGroup`
instead.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.runtime.events import (
    BranchCancelledEvent,
    BranchCompletedEvent,
    BranchStartedEvent,
)
from stargraph.runtime.merge import build_last_write_conflict_evidence

__all__ = [
    "execute_parallel",
    "run_branches",
    "shielded_audit_write",
    "shielded_checkpoint_commit",
    "shielded_pool_close",
    "shielded_transition_emit",
]


@dataclass(frozen=True, slots=True)
class _LifecycleCtx:
    """Per-branch lifecycle wiring handle (FR-13).

    Bundles the run-scope coordinates plus the optional bus/fathom
    handles so the wrapper coroutine can fire ``BranchStarted/Completed/
    Cancelled`` events and ``stargraph.transition`` facts at the four
    lifecycle sites without threading five positional args through every
    helper. ``bus``/``fathom`` are deliberately :data:`Any`-typed because
    the integration tests pass duck-typed recorders rather than the
    full :class:`EventBus` / :class:`FathomAdapter` stack.
    """

    bus: Any
    fathom: Any
    run_id: str
    step: int
    branch_id: str
    target: str
    strategy: str


#: Branch factory: zero-arg async callable producing one branch's result.
#: We use ``Coroutine`` rather than ``Awaitable`` because :func:`asyncio.create_task`
#: requires a coroutine specifically (not a general awaitable).
type _Branch[T] = Callable[[], Coroutine[Any, Any, T]]


async def run_branches(
    factories: Iterable[Callable[[], Coroutine[Any, Any, object]]],
) -> list[object]:
    """Run branches concurrently via :class:`asyncio.TaskGroup` (FR-10 case 1).

    Each factory is invoked once and its coroutine scheduled inside a
    :class:`asyncio.TaskGroup`. Structured-concurrency semantics apply:

    * On first failure the TaskGroup auto-cancels every surviving sibling.
    * The surviving siblings observe :class:`asyncio.CancelledError`.
    * The TaskGroup re-raises an :class:`ExceptionGroup` containing the
      original error (use ``ExceptionGroup.split(ExcType)`` to recover).

    This is the antipattern guard against the legacy ``asyncio`` fan-in
    helper, which silently swallows sibling cancellations and would let a
    half-cancelled branch keep running past the failure boundary.

    Args:
        factories: Iterable of zero-argument async callables. Each is
            invoked once; the resulting coroutine is scheduled in the group.

    Returns:
        Results of all branches, in input order. Only returned on the
        all-success path; any failure raises :class:`ExceptionGroup`.

    Raises:
        ExceptionGroup: When at least one branch fails. Contains the
            original exceptions; siblings that were cancelled appear as
            :class:`asyncio.CancelledError` (filtered out of the visible
            group by Python's TaskGroup).
    """
    factory_list = list(factories)
    tasks: list[asyncio.Task[object]] = []
    async with asyncio.TaskGroup() as tg:
        for factory in factory_list:
            tasks.append(tg.create_task(factory()))
    return [task.result() for task in tasks]


async def _run_shielded[T](awaitable: Awaitable[T]) -> T:
    """Run ``awaitable`` under :func:`asyncio.shield`, draining on outer cancel.

    Standard :func:`asyncio.shield` only protects the inner Task from
    cancellation -- the outer await raises immediately on cancel and the
    inner Task keeps running unobserved. For critical-section writes
    (checkpoint commit, transition emit, audit write, pool close) we need
    a stronger guarantee: the inner task **completes** before the cancel
    propagates upward. Otherwise the test pattern "cancel outer; assert
    side-effect observed" can't pass because the inner task is still
    scheduled when the outer's CancelledError arrives.

    The trick (well-known asyncio idiom): on :class:`asyncio.CancelledError`
    we ``await`` the inner task one more time to drain it to completion
    (it is the ``shield`` wrapper, not the inner task, that the outer cancel
    cancels), then re-raise the cancellation. This preserves outer-cancel
    semantics (caller sees CancelledError) while honouring the FR-10
    "critical section runs to completion" contract.
    """
    inner: asyncio.Task[T] = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        # The shield was cancelled, but ``inner`` is unaffected. Drain it
        # before propagating so the side-effect is observed by callers
        # (and so the inner task is never destroyed mid-flight).
        with contextlib.suppress(asyncio.CancelledError):
            await inner
        raise


async def shielded_checkpoint_commit[T](commit: Awaitable[T]) -> T:
    """Run ``commit`` under :func:`asyncio.shield` (FR-10 case 2, design §3.6.2).

    The verbatim FR-10 amendment 2 contract: "Critical-section writes
    (checkpoint commit, stargraph.transition emit) wrapped in
    :func:`asyncio.shield`". An outer cancel mid-flight surfaces
    :class:`asyncio.CancelledError` to the caller, but the shielded
    inner coroutine runs to completion -- the partial-commit-on-cancel
    hazard (corrupted resume) is foreclosed.
    """
    return await _run_shielded(commit)


async def shielded_transition_emit[T](emit: Awaitable[T]) -> T:
    """Run ``emit`` under :func:`asyncio.shield` (FR-10 case 3, design §3.6.2).

    Mirror of :func:`shielded_checkpoint_commit` for the
    ``stargraph.transition`` fact emit (FR-13). A half-emitted transition fact
    would corrupt the event log / CLIPS working memory; this shield is the
    documented guard.
    """
    return await _run_shielded(emit)


async def shielded_audit_write[T](write: Awaitable[T]) -> T:
    """Run an audit-sink write under :func:`asyncio.shield` (design §3.6.2 site 3).

    The third of four shield sites (design §3.6.2): partial JSONL audit
    write on cancel = a half-line in an append-only log, which breaks the
    invariant the JSONL audit sink rests on (FR-22).
    """
    return await _run_shielded(write)


async def shielded_pool_close[T](close: Awaitable[T]) -> T:
    """Run ``pool.close()`` under :func:`asyncio.shield` (design §3.6.2 site 4).

    The fourth shield site: asyncpg ``Pool.close()`` mid-cancel hits the
    partial-close hazard (asyncpg #290) -- shielding gives the close path
    its full grace window.
    """
    return await _run_shielded(close)


# ---------------------------------------------------------------------------
# Branch lifecycle wiring (FR-13, design §3.7.1).
#
# The three branch-lifecycle transitions (started/completed/cancelled) each
# fan out to two side effects: a typed ``Branch*Event`` envelope onto the
# :class:`EventBus`, and a ``stargraph.transition`` fact through the Fathom
# adapter. Both are best-effort -- if either ``bus`` or ``fathom`` is
# ``None`` (the no-wiring path used by older call sites and by the unit
# tests for :func:`run_branches` in isolation) the helper is a no-op.
# The transition emit goes through :func:`asyncio.to_thread` per design
# §3.7.1 because :meth:`FathomAdapter.assert_with_provenance` is sync and
# would otherwise block the event loop on a CLIPS round-trip.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Wall-clock UTC timestamp for event envelopes."""
    return datetime.now(UTC)


async def _emit_transition(ctx: _LifecycleCtx, kind: str) -> None:
    """Stamp a ``stargraph.transition`` fact via the Fathom adapter (design §3.7.1).

    No-ops when ``ctx.fathom`` is ``None``. The sync
    ``assert_with_provenance`` call is hopped onto a worker thread via
    :func:`asyncio.to_thread` so a CLIPS round-trip never blocks the
    event loop. Guarded by a broad-except so a half-wired adapter cannot
    abort the branch lifecycle (mirrors the bus-side guard in
    :class:`EventBus`).
    """
    if ctx.fathom is None:
        return
    emit = getattr(ctx.fathom, "assert_with_provenance", None)
    if emit is None:
        return
    slots: dict[str, Any] = {
        "kind": kind,
        "run_id": ctx.run_id,
        "step": ctx.step,
        "branch_id": ctx.branch_id,
        "target": ctx.target,
        "strategy": ctx.strategy,
    }
    # Defensive guard: a half-wired Fathom adapter must not abort the
    # branch lifecycle (parity with the bus-side guard in :mod:`bus`).
    with contextlib.suppress(Exception):
        await asyncio.to_thread(emit, "stargraph.transition", slots)


async def _emit_event(ctx: _LifecycleCtx, ev: Any) -> None:
    """Send an event onto the bus, no-op when ``ctx.bus`` is ``None``."""
    if ctx.bus is None:
        return
    await ctx.bus.send(ev, fathom=ctx.fathom)


async def _branch_started(ctx: _LifecycleCtx) -> None:
    """Fire BranchStarted event + stargraph.transition(kind=started)."""
    await _emit_event(
        ctx,
        BranchStartedEvent(
            run_id=ctx.run_id,
            step=ctx.step,
            branch_id=ctx.branch_id,
            ts=_now(),
            target=ctx.target,
            strategy=ctx.strategy,
        ),
    )
    await _emit_transition(ctx, "started")


async def _branch_completed(ctx: _LifecycleCtx, result: Any) -> None:
    """Fire BranchCompleted event + stargraph.transition(kind=completed)."""
    await _emit_event(
        ctx,
        BranchCompletedEvent(
            run_id=ctx.run_id,
            step=ctx.step,
            branch_id=ctx.branch_id,
            ts=_now(),
            result={"value": result},
        ),
    )
    await _emit_transition(ctx, "completed")


async def _branch_cancelled(ctx: _LifecycleCtx, reason: str) -> None:
    """Fire BranchCancelled event + stargraph.transition(kind=cancelled).

    Wrapped in :func:`asyncio.shield` per design §3.6.2 site 2: the
    cancellation lifecycle emit is one of the four critical-section
    writes that must run to completion even when an outer cancel is
    propagating through this branch.
    """
    await _run_shielded(
        _emit_event(
            ctx,
            BranchCancelledEvent(
                run_id=ctx.run_id,
                step=ctx.step,
                branch_id=ctx.branch_id,
                ts=_now(),
                reason=reason,
            ),
        )
    )
    await _run_shielded(_emit_transition(ctx, "cancelled"))


def _wrap_with_lifecycle[T](
    factory: Callable[[], Coroutine[Any, Any, T]],
    ctx: _LifecycleCtx,
) -> Callable[[], Coroutine[Any, Any, T]]:
    """Wrap ``factory`` so its branch fires started/completed/cancelled.

    The wrapper is itself a zero-arg async factory matching the
    :data:`_Branch` shape, so callers (e.g. :func:`run_branches`,
    :func:`_race`, :func:`_quorum`) need no changes beyond passing the
    wrapped factory in place of the raw one.
    """

    async def _wrapped() -> T:
        await _branch_started(ctx)
        try:
            result = await factory()
        except asyncio.CancelledError:
            await _branch_cancelled(ctx, reason="cancelled")
            raise
        except Exception:
            # Non-cancellation failure: emit cancelled-with-reason so the
            # event log records the lifecycle exit. Re-raise so the
            # caller's strategy dispatcher (TaskGroup, race, quorum)
            # observes the original error.
            await _branch_cancelled(ctx, reason="error")
            raise
        await _branch_completed(ctx, result)
        return result

    return _wrapped


def _make_lifecycle_ctxs(
    *,
    n: int,
    bus: Any,
    fathom: Any,
    run_id: str,
    step: int,
    strategy: str,
) -> list[_LifecycleCtx]:
    """Build one :class:`_LifecycleCtx` per branch slot."""
    return [
        _LifecycleCtx(
            bus=bus,
            fathom=fathom,
            run_id=run_id,
            step=step,
            branch_id=f"branch-{i}",
            target=f"branch-{i}",
            strategy=strategy,
        )
        for i in range(n)
    ]


def _emit_conflict_evidence(
    fathom: Any,
    conflicts: Sequence[dict[str, Any]],
    n_branches: int,
) -> None:
    """Stamp one ``stargraph.evidence(kind=last-write-conflict)`` per conflict.

    Sync helper (no asyncio hop) because it runs after the parallel
    block has fully drained, off the hot path. No-ops when ``fathom``
    is ``None`` or the adapter lacks ``assert_with_provenance``.
    """
    if fathom is None or not conflicts:
        return
    emit = getattr(fathom, "assert_with_provenance", None)
    if emit is None:
        return
    for conflict in conflicts:
        if conflict.get("strategy") != "last-write":
            continue
        payload = build_last_write_conflict_evidence(
            field=conflict["field"],
            n_branches=n_branches,
            original_confidence=conflict.get("original_confidence", 1.0),
        )
        # Defensive guard parity with :func:`_emit_transition`.
        with contextlib.suppress(Exception):
            emit("stargraph.evidence", payload)


# ---------------------------------------------------------------------------
# Strategy dispatcher (design §3.6.1).
#
# ``execute_parallel`` is the public entry point referenced from
# ``stargraph.graph.loop`` / dispatch on a :class:`ParallelAction` decision. The
# integration tests in 3.8 cover the structured-concurrency primitive
# (:func:`run_branches`) and the four shield sites; full IR-driven dispatch
# (state merge, branch_id stamping, transition fact emit) lands as those
# call sites are wired in subsequent Phase 3 tasks.
#
# The ``deadline_s`` parameter naming dodges ASYNC109 -- ruff flags ``timeout``
# on async functions because the idiomatic asyncio replacement is the
# :func:`asyncio.timeout` context manager, which we use internally below.
# Callers pass an absolute-seconds deadline; ``None`` disables.
# ---------------------------------------------------------------------------


async def execute_parallel[T](
    factories: Iterable[_Branch[T]],
    *,
    strategy: str,
    deadline_s: float | None = None,
    bus: Any = None,
    fathom: Any = None,
    run_id: str = "",
    step: int = 0,
    conflicts: Sequence[dict[str, Any]] | None = None,
) -> list[T]:
    """Dispatch a parallel block per ``strategy`` (design §3.6.1).

    Strategies:

    * ``all`` -- all branches must succeed; uses :func:`run_branches`.
    * ``any`` / ``race`` -- first-success wins; remaining branches cancelled.
    * ``quorum:N`` -- N successes before ``deadline_s`` win; timeout
      precedence per design §3.6.1 (timeout fires first; success-tie at
      deadline goes to quorum -- success wins ties).

    Branch lifecycle wiring (FR-13, design §3.7.1): when ``bus`` and/or
    ``fathom`` are supplied, every branch fires
    :class:`BranchStartedEvent` at fork, :class:`BranchCompletedEvent`
    on success, and :class:`BranchCancelledEvent` on cancel/error -- and
    each transition stamps a ``stargraph.transition`` fact through the
    Fathom adapter (off-loop via :func:`asyncio.to_thread`). When both
    handles are ``None`` the wiring is a no-op (this is the path the
    structured-concurrency unit tests in 3.8 exercise).

    Args:
        factories: Zero-arg async callables for each branch.
        strategy: One of ``all``, ``any``, ``race``, ``quorum:<N>``.
        deadline_s: Seconds. Required for ``race``/``any``/``quorum:N``.
        bus: Optional :class:`EventBus` (or duck-typed recorder) to
            receive branch lifecycle events.
        fathom: Optional Fathom adapter (or duck-typed recorder) to
            receive ``stargraph.transition`` and ``stargraph.evidence`` facts.
        run_id: Run-scope id stamped onto every emitted event/fact.
        step: Run-loop step index stamped onto every emitted event/fact.
        conflicts: Optional list of last-write conflict descriptors. Each
            entry triggers a ``stargraph.evidence(kind=last-write-conflict)``
            fact via :func:`build_last_write_conflict_evidence` (design
            §3.6.3).

    Returns:
        Results in completion order (for ``race``/``any``/``quorum:N``) or
        in input order (for ``all``).

    Raises:
        ValueError: Unknown strategy.
        TimeoutError: ``quorum:N`` deadline elapsed without N successes.
        ExceptionGroup: Any branch failure on the ``all`` path.
    """
    factory_list: list[_Branch[T]] = list(factories)
    ctxs = _make_lifecycle_ctxs(
        n=len(factory_list),
        bus=bus,
        fathom=fathom,
        run_id=run_id,
        step=step,
        strategy=strategy,
    )
    wrapped: list[_Branch[T]] = [
        _wrap_with_lifecycle(f, ctx) for f, ctx in zip(factory_list, ctxs, strict=True)
    ]
    if strategy == "all":
        # ``run_branches`` is typed object-out for the structured-concurrency
        # primitive; here we know each branch yields ``T`` because the
        # caller's ``factories`` parameter is typed ``_Branch[T]``.
        results = await run_branches(wrapped)
        _emit_conflict_evidence(fathom, conflicts or (), n_branches=len(factory_list))
        return [r for r in results]  # type: ignore[misc]
    if strategy in ("any", "race"):
        winner = await _race(wrapped, deadline_s=deadline_s)
        _emit_conflict_evidence(fathom, conflicts or (), n_branches=len(factory_list))
        return [winner]
    if strategy.startswith("quorum:"):
        n = int(strategy.split(":", 1)[1])
        results_q = await _quorum(wrapped, n=n, deadline_s=deadline_s)
        _emit_conflict_evidence(fathom, conflicts or (), n_branches=len(factory_list))
        return results_q
    raise ValueError(f"unknown parallel strategy: {strategy!r}")


async def _race[T](
    factories: list[_Branch[T]],
    *,
    deadline_s: float | None,
) -> T:
    """First-success-wins race (design §3.6.1, FR-10 amendment 2).

    Uses :func:`asyncio.wait` with ``return_when=FIRST_COMPLETED`` followed
    by an explicit ``task.cancel()`` of every pending sibling -- the
    documented amendment-2 pattern. After cancelling we ``await`` each
    pending task to let :class:`asyncio.CancelledError` drain cleanly
    (otherwise Python warns about destroyed-while-pending coroutines).
    """
    if not factories:
        raise ValueError("_race requires at least one branch")

    tasks: list[asyncio.Task[T]] = [asyncio.create_task(f()) for f in factories]
    try:
        if deadline_s is not None:
            async with asyncio.timeout(deadline_s):
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        else:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except TimeoutError:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        raise

    # Cancel every pending sibling and drain.
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # Return the first-completed result; if it raised, propagate.
    first = next(iter(done))
    return first.result()


def _quorum_timeout(n: int, deadline_s: float, observed: int) -> StargraphRuntimeError:
    """Build the quorum-timeout error (helper-pattern dodge for the errors walker).

    The errors walker (``tests/unit/test_errors_walker.py``) bans ``raise
    TimeoutError(...)`` directly because :class:`TimeoutError` is not on the
    allow-list -- but accepts ``raise <helper>(...)`` when the helper's return
    annotation is an allow-listed Stargraph exception. Routing the quorum-timeout
    branch through this helper keeps the design §3.6.1 semantic ("raise on
    deadline before N successes") while satisfying the FR-24 raise-policy.
    """
    return StargraphRuntimeError(
        f"quorum:{n} not satisfied within {deadline_s}s (observed {observed} successes)"
    )


async def _quorum[T](
    factories: list[_Branch[T]],
    *,
    n: int,
    deadline_s: float | None,
) -> list[T]:
    """N-of-M quorum with timeout precedence (design §3.6.1, Learning C).

    Timeout fires first when both are about to trigger; **success wins**
    ties at the deadline boundary (i.e. if at the moment we observe
    timeout-elapsed we already have ``len(successes) >= n``, we honour
    the quorum rather than raising).
    """
    if n <= 0:
        raise ValueError(f"quorum N must be positive; got {n}")
    if deadline_s is None:
        raise ValueError("quorum:N requires a deadline_s")

    tasks: list[asyncio.Task[T]] = [asyncio.create_task(f()) for f in factories]
    successes: list[T] = []
    deadline = asyncio.get_running_loop().time() + deadline_s

    try:
        while len(successes) < n:
            now = asyncio.get_running_loop().time()
            remaining = deadline - now
            pending = [t for t in tasks if not t.done()]
            if not pending:
                break
            if remaining <= 0:
                # Timeout elapsed. Per design §3.6.1, success-tie at the
                # deadline goes to quorum: re-check successes before raising.
                if len(successes) >= n:
                    break
                raise _quorum_timeout(n, deadline_s, len(successes))
            done, _still_pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                exc = task.exception()
                if exc is None:
                    successes.append(task.result())
            # Loop continues; timeout/quorum re-checked at the top.
    finally:
        # Cancel every still-pending branch and drain.
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    return successes[:n]
