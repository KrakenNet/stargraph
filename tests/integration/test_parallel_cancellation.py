# SPDX-License-Identifier: Apache-2.0
"""FR-10 parallel cancellation integration tests (verbatim amendment 2).

Asserts the four behaviours required by ``requirements.md §FR-10`` (verbatim
amendment 2 from research §4) and the parallel/join executor that lives at
:mod:`stargraph.runtime.parallel`:

1. ``asyncio.TaskGroup`` propagates :class:`ExceptionGroup` on first failure
   and auto-cancels sibling branches (structured-concurrency invariant -- the
   antipattern guard against ``asyncio.gather`` cancellation leaks).
2. ``asyncio.shield()`` protects checkpoint commit during sibling cancellation
   -- the commit completes even when the outer task is cancelled mid-flight
   (verbatim from FR-10 amendment 2: "Critical-section writes (checkpoint
   commit, stargraph.transition emit) wrapped in asyncio.shield()").
3. ``asyncio.shield()` analogously protects ``stargraph.transition`` emit so a
   lost cancel race never leaves a half-emitted transition fact behind.
4. **Static guard:** :mod:`stargraph.runtime.parallel` MUST NOT contain the
   string ``asyncio.gather`` (search-and-fail). ``gather`` is forbidden in
   the parallel executor per amendment 2 -- only test fixtures and the
   explicit-opt-in ``return_exceptions=True`` path may use it.

This is the [TDD-RED] half of the antipattern guard: ``stargraph.runtime.parallel``
does not yet exist (created in task 3.9 [TDD-GREEN]), so importing it raises
``ImportError`` -- the verify gate ``grep -qE "(FAILED|ERROR)"`` matches that.
Test 4 (the grep guard) gracefully skips while the file is missing so the GREEN
task immediately starts running the real assertion the moment it lands.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

import pytest

_PARALLEL_PATH = Path("src/stargraph/runtime/parallel.py")


def _import_parallel() -> Any:
    """Deferred-import helper for ``stargraph.runtime.parallel``.

    Mirrors the pattern in :mod:`tests.integration.test_dspy_loud_fallback`:
    routing the import through :func:`importlib.import_module` keeps pyright
    happy in the [TDD-RED] state (the module does not yet exist) while still
    surfacing :class:`ImportError` at runtime as the RED signal that the
    verify gate ``grep -qE "(FAILED|ERROR)"`` matches.
    """
    return importlib.import_module("stargraph.runtime.parallel")


@pytest.mark.asyncio
async def test_taskgroup_propagates_exception_group_and_cancels_siblings() -> None:
    """FR-10 case 1: ``TaskGroup`` raises ``ExceptionGroup``; siblings cancelled.

    The verbatim amendment 2 recipe says branch execution uses
    ``asyncio.TaskGroup`` (not ``gather``) so the first failure aborts the
    whole group via structured concurrency. We assert (a) the public entry
    point ``run_branches`` raises an ``ExceptionGroup`` containing the
    original failure and (b) the surviving sibling observed
    :class:`asyncio.CancelledError` (auto-cancel on group abort).
    """
    parallel = _import_parallel()
    run_branches = parallel.run_branches

    sibling_cancelled = asyncio.Event()

    async def failing_branch() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("branch-1 boom")

    async def long_running_sibling() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    with pytest.raises(ExceptionGroup) as excinfo:
        await run_branches([failing_branch, long_running_sibling])

    # The original RuntimeError must surface inside the ExceptionGroup -- this
    # is the core "structured concurrency" guarantee TaskGroup provides over
    # gather (which would silently swallow sibling cancellations).
    matched, _ = excinfo.value.split(RuntimeError)
    assert matched is not None, "ExceptionGroup did not contain the RuntimeError"

    # And the sibling MUST observe the auto-cancel TaskGroup fires on abort.
    assert sibling_cancelled.is_set(), (
        "TaskGroup did not auto-cancel the surviving sibling on first failure"
    )


@pytest.mark.asyncio
async def test_shield_protects_checkpoint_commit_under_outer_cancel() -> None:
    """FR-10 case 2: shielded checkpoint commit completes despite outer cancel.

    Verbatim amendment 2: "Critical-section writes (checkpoint commit,
    stargraph.transition emit) wrapped in asyncio.shield()". The outer task is
    cancelled mid-commit; the shielded inner coroutine MUST run to completion
    and the commit-side-effect MUST be observed (here: a ``commit_done``
    sentinel set inside the shielded coroutine). Without ``asyncio.shield`` the
    commit would be cancelled mid-flight and leave the checkpoint half-written.
    """
    parallel = _import_parallel()
    shielded_checkpoint_commit = parallel.shielded_checkpoint_commit

    commit_started = asyncio.Event()
    commit_done = asyncio.Event()

    async def slow_commit() -> str:
        commit_started.set()
        # Simulate a multi-step write that an outer cancel would otherwise
        # interrupt half-way through.
        await asyncio.sleep(0.05)
        commit_done.set()
        return "checkpoint-step-7"

    async def outer() -> str:
        # ``shielded_checkpoint_commit`` is the production helper that wraps
        # the commit coroutine in ``asyncio.shield(...)``.
        return await shielded_checkpoint_commit(slow_commit())

    task: asyncio.Task[str] = asyncio.create_task(outer())
    # Wait until the inner commit is actually running before we cancel.
    await commit_started.wait()
    task.cancel()

    # Outer task surfaces CancelledError ...
    with pytest.raises(asyncio.CancelledError):
        await task

    # ... but the shielded inner coroutine MUST have finished its commit.
    assert commit_done.is_set(), (
        "asyncio.shield did not protect the checkpoint commit from outer cancel"
    )


@pytest.mark.asyncio
async def test_shield_protects_transition_emit_under_outer_cancel() -> None:
    """FR-10 case 3: shielded ``stargraph.transition`` emit survives outer cancel.

    Mirror of case 2 but for the ``stargraph.transition`` emit critical section
    (FR-13). A half-emitted transition fact would corrupt the event log /
    CLIPS working memory; ``asyncio.shield`` is the documented guard.
    """
    parallel = _import_parallel()
    shielded_transition_emit = parallel.shielded_transition_emit

    emit_started = asyncio.Event()
    emit_done = asyncio.Event()

    async def slow_emit() -> None:
        emit_started.set()
        await asyncio.sleep(0.05)
        emit_done.set()

    async def outer() -> None:
        await shielded_transition_emit(slow_emit())

    task: asyncio.Task[None] = asyncio.create_task(outer())
    await emit_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert emit_done.is_set(), "asyncio.shield did not protect the stargraph.transition emit"


def test_parallel_module_does_not_use_asyncio_gather() -> None:
    """FR-10 case 4 (static guard): ``asyncio.gather`` forbidden in parallel.py.

    Verbatim amendment 2: "``gather`` only used in test fixtures and where
    opt-in ``return_exceptions=True`` is *required* (with explicit
    ``CancelledError`` re-raise)." The parallel executor is the antipattern-
    sensitive surface, so we statically grep for the string ``asyncio.gather``
    and fail-loud if it appears. While the file is missing (RED state) we skip
    -- the moment 3.9 GREEN creates the file the assertion starts running and
    pins the invariant against future regressions.
    """
    try:
        source: str = _PARALLEL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        pytest.skip(
            f"{_PARALLEL_PATH} does not exist yet (TDD-RED for task 3.8); "
            "the static gather-guard activates once task 3.9 GREEN creates the file"
        )
    assert "asyncio.gather" not in source, (
        f"FR-10 amendment 2 forbids asyncio.gather in {_PARALLEL_PATH}; "
        "use asyncio.TaskGroup instead"
    )
