# SPDX-License-Identifier: Apache-2.0
"""FR-15 streaming back-pressure integration tests (TDD-RED).

Asserts the four behaviours required by ``requirements.md §FR-15``
(verbatim amendment 7) and ``design.md §3.7.2``:

1. A slow consumer (sleep 0.5s/recv) causes the producer to block on
   ``send`` once the bounded 256-slot buffer fills (back-pressure --
   no overflow exception, no drop, just blocking).
2. After a sustained block exceeding the configured threshold, the
   bus emits a ``stargraph.evidence(kind="stream-backpressure",
   buffer_used=256, max=256, block_seconds=...)`` fact via the
   :class:`FathomAdapter` (AC-7.2).
3. NO tokens are dropped: the count of events the producer pushed
   equals the count the consumer drained (AC-7.3, "no drops at engine
   layer").
4. Buffer size invariants: :class:`EventBus` rejects ``0`` (deadlocks
   slow-consumer scenarios) and ``math.inf`` (explicit anti-pattern
   per anyio's own docs); only positive finite ints are accepted.

This is the [TDD-RED] half: :class:`stargraph.runtime.bus.EventBus` ships
the bounded buffer + best-effort >5s emit (task 1.15), but the surface
required by the FR-15 amendment 7 contract is not fully wired yet:

- the constructor does not yet accept a ``backpressure_threshold_s``
  parameter, so test 2 cannot drive the >5s emit without waiting a
  real five seconds (intentional RED -- task 3.18 GREEN adds the
  injectable threshold);
- the constructor does not yet accept a ``max_buffer_size`` kwarg with
  validation (rejects ``0``/``math.inf``), so test 4 fails with
  ``TypeError`` instead of ``ValueError`` (intentional RED).

Tests 1 and 3 exercise the existing 256-slot bus end-to-end -- those
should pass against the current implementation and document the
"no-drop" contract for regression. The full quartet only goes green
once 3.18 wires the constructor surface.

Test fixture style mirrors :mod:`tests.integration.test_branch_lifecycle_facts`:
- Imports :mod:`stargraph.runtime.bus` via :func:`importlib.import_module`
  to keep pyright strict-mode green when the new constructor surface
  is missing.
- Uses a lightweight stub fathom recorder (the bus only needs
  ``assert_with_provenance``); no full CLIPS engine spin-up.
"""

from __future__ import annotations

import importlib
import math
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from stargraph.runtime.events import TokenEvent


def _import_bus() -> Any:
    """Deferred-import helper for ``stargraph.runtime.bus`` (RED-safe)."""
    return importlib.import_module("stargraph.runtime.bus")


class _RecordingFathom:
    """Minimal Fathom adapter recorder.

    Captures every ``assert_with_provenance(template, slots, ...)``
    call so the test can assert ``stargraph.evidence`` emits without
    depending on the full CLIPS engine.
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
        self.facts.append((template, dict(slots)))

    def evidence(self) -> list[dict[str, Any]]:
        return [slots for tpl, slots in self.facts if tpl == "stargraph.evidence"]


def _token(idx: int) -> TokenEvent:
    """Construct a deterministic :class:`TokenEvent` with ``idx`` ordinal."""
    return TokenEvent(
        run_id="run-bp",
        step=idx,
        ts=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        model="stub",
        token=f"t{idx}",
        index=idx,
    )


# ---- Test 1: slow consumer drives producer into back-pressure block ----


async def test_slow_consumer_blocks_producer_on_full_buffer() -> None:
    """Once the 256-slot buffer is full, ``send`` blocks (no drop, no exc).

    The producer pushes 257 events without a consumer running. The
    257th send must NOT return within a short timeout (it is awaiting
    a free slot). ``move_on_after`` lets the test verify the block
    without stalling indefinitely; cancelling the producer task is
    safe because the test owns the bus.
    """
    bus_mod = _import_bus()
    bus = bus_mod.EventBus()
    fathom = _RecordingFathom()

    # Fill the buffer to capacity (256 slots).
    for i in range(256):
        await bus.send(_token(i), fathom=fathom)

    # The 257th send must block (back-pressure). Use move_on_after to
    # bound the wait; if the call returns before timeout the buffer
    # accepted it, which would mean either the bound is wrong or the
    # implementation silently drops -- both are bugs.
    blocked = True
    with anyio.move_on_after(0.25) as scope:
        await bus.send(_token(256), fathom=fathom)
        blocked = False
    assert scope.cancel_called, (
        "send() returned within 0.25s -- producer was NOT blocked by "
        "back-pressure (bounded buffer contract violated)"
    )
    assert blocked


# ---- Test 2: sustained block emits stargraph.evidence(stream-backpressure) ----


async def test_sustained_block_emits_stargraph_evidence_fact() -> None:
    """Block longer than the threshold -> ``stargraph.evidence`` fact emitted.

    Drives the bus with a low ``backpressure_threshold_s`` (injected
    via constructor) so the test does not wait a real 5 seconds. The
    consumer sleeps 0.5s between recvs; the producer's last few sends
    therefore each block well past the 0.1s threshold. After the run,
    the recorded fathom asserts must include at least one
    ``stargraph.evidence(kind="stream-backpressure", buffer_used=256, max=256)``.
    """
    bus_mod = _import_bus()
    # Constructor surface required by FR-15 amendment 7 -- task 3.18
    # GREEN wires this. Currently EventBus.__init__() takes no kwargs
    # so this call raises TypeError (RED).
    bus = bus_mod.EventBus(backpressure_threshold_s=0.1)
    fathom = _RecordingFathom()

    n = 260  # 256 buffer + 4 forced blocks past the threshold

    async def producer() -> None:
        for i in range(n):
            await bus.send(_token(i), fathom=fathom)

    async def slow_consumer() -> int:
        drained = 0
        for _ in range(n):
            await bus.receive()
            drained += 1
            await anyio.sleep(0.5)
        return drained

    async with anyio.create_task_group() as tg:
        tg.start_soon(producer)
        tg.start_soon(slow_consumer)

    evidence = fathom.evidence()
    assert any(
        slots.get("kind") == "stream-backpressure"
        and slots.get("buffer_used") == 256
        and slots.get("max") == 256
        for slots in evidence
    ), (
        f"expected stargraph.evidence(kind='stream-backpressure', "
        f"buffer_used=256, max=256); got {evidence!r}"
    )


# ---- Test 3: no drops -- count produced == count consumed ----


async def test_no_tokens_dropped_under_backpressure() -> None:
    """AC-7.3: count produced equals count consumed -- zero drops.

    Pushes ``n=300`` tokens through the 256-slot bus with a slow
    consumer. The producer back-pressure must serialize cleanly; no
    overflow exception, no silent drop. The receiver index sequence
    must equal the producer's emit order.
    """
    bus_mod = _import_bus()
    bus = bus_mod.EventBus()
    fathom = _RecordingFathom()

    n = 300
    received: list[int] = []

    async def producer() -> None:
        for i in range(n):
            await bus.send(_token(i), fathom=fathom)

    async def slow_consumer() -> None:
        for _ in range(n):
            ev = await bus.receive()
            received.append(ev.index)
            await anyio.sleep(0.01)

    async with anyio.create_task_group() as tg:
        tg.start_soon(producer)
        tg.start_soon(slow_consumer)

    assert len(received) == n, f"expected {n} events, got {len(received)} -- bus dropped tokens"
    assert received == list(range(n)), (
        "received order does not match produced order -- bus reordered or dropped"
    )


# ---- Test 4: buffer-size invariants (never 0, never inf) ----


def test_eventbus_rejects_zero_buffer_size() -> None:
    """``EventBus(max_buffer_size=0)`` MUST raise ``ValueError``.

    A zero-slot buffer deadlocks the producer against any non-trivial
    consumer pattern (FR-15 amendment 7 verbatim).
    """
    bus_mod = _import_bus()
    with pytest.raises(ValueError):
        bus_mod.EventBus(max_buffer_size=0)


def test_eventbus_rejects_infinite_buffer_size() -> None:
    """``EventBus(max_buffer_size=math.inf)`` MUST raise ``ValueError``.

    Unbounded buffers nullify back-pressure -- the explicit anti-pattern
    called out in anyio's docs (FR-15 amendment 7 verbatim).
    """
    bus_mod = _import_bus()
    with pytest.raises(ValueError):
        bus_mod.EventBus(max_buffer_size=math.inf)


def test_eventbus_accepts_positive_finite_int_buffer() -> None:
    """``EventBus(max_buffer_size=<positive int>)`` must construct cleanly."""
    bus_mod = _import_bus()
    bus = bus_mod.EventBus(max_buffer_size=8)
    assert bus is not None
