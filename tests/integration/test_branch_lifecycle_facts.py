# SPDX-License-Identifier: Apache-2.0
"""FR-13 branch lifecycle facts integration tests (TDD-RED).

Asserts the three behaviours required by ``requirements.md §FR-13`` and
``design.md §3.7.1``:

1. A parallel block with 3 branches emits 3 :class:`BranchStartedEvent` and
   3 :class:`BranchCompletedEvent` (or :class:`BranchCancelledEvent`) into
   ``run.stream()`` -- one per branch fork/join.
2. ``stargraph.transition`` facts are emitted via the Fathom adapter on each
   branch start, completion, and cancellation transition.
3. ``stargraph.evidence(kind="last-write-conflict")`` is emitted on an
   un-reduced parallel write that resolves through the ``last-write``
   strategy.

This is the [TDD-RED] half: :mod:`stargraph.runtime.parallel` ships
:func:`execute_parallel` (from task 3.9) but does NOT yet wire
:class:`BranchStartedEvent` / :class:`BranchCompletedEvent` /
:class:`BranchCancelledEvent` into the run's :class:`EventBus`, nor does
it stamp ``stargraph.transition`` / ``stargraph.evidence`` facts via the
:class:`FathomAdapter` at branch boundaries. Tests fail because the
public surface for ``execute_parallel(..., bus=, fathom=)`` (or
equivalent emit hook) does not exist; the [TDD-GREEN] follow-up
(task 3.16) wires the emissions.

Test fixture style mirrors :mod:`tests.integration.test_parallel_cancellation`:
- Imports ``stargraph.runtime.parallel`` via :func:`importlib.import_module`
  to keep pyright strict-mode green when the surface is missing.
- Uses lightweight stub bus + fathom recorders rather than a full
  ``Graph`` end-to-end. The full IR-driven run-loop integration lands in
  later phases; what matters here is that the parallel executor accepts
  the bus/fathom handles and emits the documented events/facts at the
  correct branch transitions.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from typing import Any

import pytest


def _import_parallel() -> Any:
    """Deferred-import helper for ``stargraph.runtime.parallel`` (RED-safe)."""
    return importlib.import_module("stargraph.runtime.parallel")


class _RecordingBus:
    """Minimal in-memory event recorder.

    Mirrors the :class:`stargraph.runtime.bus.EventBus` send surface enough
    that ``execute_parallel`` can publish without spinning a real anyio
    stream. Records every send for post-hoc assertion.
    """

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def send(self, ev: Any, *, fathom: Any = None) -> None:
        del fathom
        self.events.append(ev)

    def types(self) -> list[str | None]:
        return [getattr(ev, "type", None) for ev in self.events]


class _RecordingFathom:
    """Minimal Fathom adapter recorder.

    Captures every ``assert_with_provenance(template, slots, provenance)``
    call so the test can assert ``stargraph.transition`` / ``stargraph.evidence``
    emits at branch boundaries without depending on the full CLIPS engine.
    """

    def __init__(self) -> None:
        self.facts: list[tuple[str, dict[str, Any]]] = []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: Any = None,
    ) -> None:
        del provenance
        self.facts.append((template, slots))

    def transitions(self) -> list[dict[str, Any]]:
        return [slots for tpl, slots in self.facts if tpl == "stargraph.transition"]

    def evidence(self) -> list[dict[str, Any]]:
        return [slots for tpl, slots in self.facts if tpl == "stargraph.evidence"]


def _make_factory(value: int, delay: float = 0.0) -> Any:
    """Build a zero-arg async branch factory yielding ``value`` after ``delay``."""

    async def _branch() -> int:
        if delay > 0:
            await asyncio.sleep(delay)
        return value

    return _branch


@pytest.mark.asyncio
async def test_three_branches_emit_started_and_completed_events() -> None:
    """FR-13 case 1: 3 branches -> 3 BranchStarted + 3 BranchCompleted events.

    A parallel block with three independent branches under the ``all``
    strategy MUST publish exactly three :class:`BranchStartedEvent` and
    three :class:`BranchCompletedEvent` envelopes into the run's event
    bus. Order between fork events vs join events is not pinned (branches
    run concurrently); only the per-type counts are asserted.
    """
    parallel = _import_parallel()
    execute_parallel = parallel.execute_parallel

    bus = _RecordingBus()
    fathom = _RecordingFathom()

    factories = [_make_factory(i) for i in range(3)]
    # The RED contract: ``execute_parallel`` does not yet accept
    # ``bus=`` / ``fathom=`` keyword args (task 3.9 ships the signature
    # without them; task 3.16 GREEN adds them). The test fails at
    # ``TypeError: unexpected keyword argument 'bus'`` until the GREEN
    # wires the emit path.
    results = await execute_parallel(
        factories,
        strategy="all",
        bus=bus,
        fathom=fathom,
        run_id="test-run-1",
        step=0,
    )
    assert sorted(results) == [0, 1, 2]

    started = [t for t in bus.types() if t == "branch_started"]
    completed = [t for t in bus.types() if t == "branch_completed"]
    assert len(started) == 3, f"expected 3 BranchStartedEvent, got {len(started)}"
    assert len(completed) == 3, f"expected 3 BranchCompletedEvent, got {len(completed)}"


@pytest.mark.asyncio
async def test_branch_transitions_emit_stargraph_transition_facts() -> None:
    """FR-13 case 2: ``stargraph.transition`` facts at start / complete / cancel.

    Per design §3.7.1, every branch lifecycle transition (``-> started``,
    ``-> completed``, ``-> cancelled``) MUST be reflected as a
    ``stargraph.transition`` fact via the Fathom adapter. We use a ``race``
    strategy so at least one branch is cancelled (loser of the race) and
    we can observe the cancel-side transition fact.
    """
    parallel = _import_parallel()
    execute_parallel = parallel.execute_parallel

    bus = _RecordingBus()
    fathom = _RecordingFathom()

    # Fast winner + slow losers -> cancelled siblings. The winner gets a
    # small non-zero delay: with delay=0.0 it completes synchronously on
    # its first tick, and a loser task can be cancelled before the event
    # loop ever starts it -- emitting neither a 'started' nor a
    # 'cancelled' transition (observed as a flake on loaded CI runners).
    # One yield is enough for every branch to enter its coroutine and
    # emit 'started' before the winner finishes.
    factories = [
        _make_factory(0, delay=0.05),
        _make_factory(1, delay=10.0),
        _make_factory(2, delay=10.0),
    ]
    await execute_parallel(
        factories,
        strategy="race",
        deadline_s=5.0,
        bus=bus,
        fathom=fathom,
        run_id="test-run-2",
        step=0,
    )

    transitions = fathom.transitions()
    # Need at least: 3 starts + 1 complete + 2 cancels = 6 transitions.
    kinds = [slots.get("kind") for slots in transitions]
    assert kinds.count("started") >= 3, f"expected >=3 'started' transitions, got {kinds}"
    assert kinds.count("completed") >= 1, f"expected >=1 'completed' transition, got {kinds}"
    assert kinds.count("cancelled") >= 2, f"expected >=2 'cancelled' transitions, got {kinds}"


@pytest.mark.asyncio
async def test_last_write_conflict_emits_stargraph_evidence_fact() -> None:
    """FR-13 case 3: un-reduced last-write conflict emits stargraph.evidence.

    Combined with task 3.11's confidence-decay test: an un-reduced field
    written by multiple parallel branches under the ``last-write``
    strategy MUST emit a ``stargraph.evidence(kind="last-write-conflict")``
    fact carrying the decayed confidence per design §3.6.3.

    The merge module already exposes :func:`build_last_write_conflict_evidence`
    as a pure helper; what's missing is the runtime wiring that calls it
    from inside :func:`execute_parallel` when the ``last-write`` reducer
    resolves a conflict. The RED contract: the parallel executor does not
    yet accept a ``conflicts=`` argument (or equivalent merge-context
    handle) that would let it emit the evidence fact -- the test fails
    at ``TypeError`` or at the missing ``stargraph.evidence`` fact.
    """
    parallel = _import_parallel()
    execute_parallel = parallel.execute_parallel

    bus = _RecordingBus()
    fathom = _RecordingFathom()

    # Two branches both writing to the same field via last-write.
    factories = [
        _make_factory(10),
        _make_factory(20),
    ]
    await execute_parallel(
        factories,
        strategy="all",
        bus=bus,
        fathom=fathom,
        run_id="test-run-3",
        step=0,
        # The RED-only kwarg: a conflict descriptor the GREEN path uses
        # to drive ``build_last_write_conflict_evidence`` and emit the
        # ``stargraph.evidence`` fact. Task 3.16 GREEN defines the shape.
        conflicts=[
            {
                "field": "result",
                "strategy": "last-write",
                "original_confidence": 0.9,
            }
        ],
    )

    evidence = fathom.evidence()
    last_write = [slots for slots in evidence if slots.get("kind") == "last-write-conflict"]
    assert len(last_write) >= 1, (
        f"expected >=1 stargraph.evidence(kind=last-write-conflict), got {evidence}"
    )
    payload = last_write[0]
    assert payload.get("field") == "result"
    assert "merged_confidence" in payload
    assert payload["merged_confidence"] < payload.get("original_confidence", 1.0)


def test_envelope_shape_for_branch_events_unused_helper() -> None:
    """Sanity guard: import the three branch event types from the vocabulary.

    Not strictly part of FR-13 but pins that the events module exposes the
    three branch lifecycle types (FR-14) the GREEN wiring will publish.
    Fails loudly if the vocabulary regresses.
    """
    from stargraph.runtime.events import (
        BranchCancelledEvent,
        BranchCompletedEvent,
        BranchStartedEvent,
    )

    started = BranchStartedEvent(
        run_id="r",
        step=0,
        branch_id="b1",
        ts=datetime.now(UTC),
        target="node-x",
        strategy="all",
    )
    completed = BranchCompletedEvent(
        run_id="r",
        step=0,
        branch_id="b1",
        ts=datetime.now(UTC),
        result={"value": 1},
    )
    cancelled = BranchCancelledEvent(
        run_id="r",
        step=0,
        branch_id="b1",
        ts=datetime.now(UTC),
        reason="race-loser",
    )
    assert started.type == "branch_started"
    assert completed.type == "branch_completed"
    assert cancelled.type == "branch_cancelled"
