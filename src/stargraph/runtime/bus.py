# SPDX-License-Identifier: Apache-2.0
"""Bounded event bus with back-pressure (FR-15, design §3.7.2).

Wraps :func:`anyio.create_memory_object_stream` with the verbatim
contract of FR-15 amendment 7: ``max_buffer_size=256`` (never ``0``,
which deadlocks slow-consumer scenarios; never ``math.inf``, which is
the explicit anti-pattern called out in anyio's own docs because it
defeats the whole point of back-pressure). Producers call
:meth:`EventBus.send` which tries :meth:`send_nowait` first and falls
through to a blocking :meth:`send` on :class:`anyio.WouldBlock`. While
the buffer is full the producer blocks (back-pressure), and on
block-detection above the configured threshold (default 5s per FR-15
amendment 7) the bus emits a ``stargraph.evidence`` fact via the Fathom
adapter (AC-7.2) -- no drops, no overflow exceptions in v1. Edge drop
policy lives at serve-and-bosun, not here.

The Fathom emit is dispatched through :func:`asyncio.to_thread` per
design §3.7.2 verbatim, so a sync CLIPS engine cannot stall the async
event loop. The ``fathom`` parameter is typed as :data:`Any` because
``stargraph.fathom.FathomAdapter.assert_with_provenance`` is a sync method
on a non-Protocol class; the bus only needs the structural surface
``assert_with_provenance(template, slots, provenance)``. The emit is
guarded by ``try/except`` so a missing/half-wired adapter cannot crash
the producer.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from typing import TYPE_CHECKING, Any

import anyio

from stargraph.runtime.events import Event

if TYPE_CHECKING:
    from anyio.streams.memory import (
        MemoryObjectReceiveStream,
        MemoryObjectSendStream,
    )

__all__ = ["EventBus"]

#: Default buffer size per FR-15 amendment 7.
_DEFAULT_BUFFER_SIZE = 256

#: Default threshold (seconds) above which a sustained block emits a
#: ``stargraph.evidence`` fact (AC-7.2, FR-15 amendment 7 verbatim).
_DEFAULT_BACKPRESSURE_THRESHOLD_S = 5.0


class EventBus:
    """anyio bounded memory stream with single-receiver back-pressure (FR-15).

    One receiver per :class:`stargraph.GraphRun`; senders may be cloned
    if multiple producers (e.g. parallel branches) need to push events
    concurrently. Back-pressure is enforced by the bounded buffer:
    when full, :meth:`send` awaits the receiver draining at least one
    slot before resuming.

    Buffer size defaults to 256 (FR-15 amendment 7 verbatim). The
    constructor rejects ``0`` (deadlocks the producer against any
    non-trivial slow consumer) and ``math.inf`` (anti-pattern called
    out in anyio's own docs because it nullifies back-pressure
    entirely); only positive finite ints are accepted.

    The back-pressure-evidence threshold defaults to 5.0 seconds (FR-15
    amendment 7) but is constructor-injectable for testability so
    integration tests do not have to wait a real five seconds to
    exercise the >threshold emit path.
    """

    def __init__(
        self,
        *,
        max_buffer_size: int = _DEFAULT_BUFFER_SIZE,
        backpressure_threshold_s: float = _DEFAULT_BACKPRESSURE_THRESHOLD_S,
    ) -> None:
        # Reject the two explicit anti-patterns from FR-15 amendment 7.
        # The annotation says ``int`` but the test (and downstream
        # callers in the wild) can pass ``math.inf`` (float) or ``0``
        # (int) -- both must ValueError at runtime. ``bool`` is a
        # subclass of ``int`` so True/False would otherwise sneak past
        # the type check.
        size: Any = max_buffer_size
        if not isinstance(size, int) or isinstance(size, bool):
            raise ValueError(
                f"max_buffer_size must be a positive finite int; got {max_buffer_size!r}"
            )
        if size <= 0 or not math.isfinite(size):
            raise ValueError(
                f"max_buffer_size must be a positive finite int; got {max_buffer_size!r}"
            )
        self._max_buffer_size = max_buffer_size
        self._backpressure_threshold_s = backpressure_threshold_s
        # Typed generic stream (anyio 4.x supports the [Event] subscript).
        send: MemoryObjectSendStream[Event]
        recv: MemoryObjectReceiveStream[Event]
        send, recv = anyio.create_memory_object_stream[Event](max_buffer_size=max_buffer_size)
        self._send = send
        self._recv = recv
        # ``None`` whenever the buffer is not currently being held in a
        # blocked-send state; set to ``anyio.current_time()`` on the
        # first WouldBlock and cleared once the blocking send returns.
        self._block_started_at: float | None = None

    async def send(self, ev: Event, *, fathom: Any) -> None:
        """Send an event with back-pressure (design §3.7.2 verbatim).

        Tries non-blocking send first; on :class:`anyio.WouldBlock`
        falls through to a blocking send and records the block start
        time. If the resulting block exceeds the configured threshold
        (default 5s, FR-15 amendment 7), emits a ``stargraph.evidence``
        fact via :func:`asyncio.to_thread` to the Fathom adapter
        (AC-7.2). The emit is best-effort: a missing/broken adapter
        must never crash the producer (no drops, no overflow exceptions
        in v1).

        Args:
            ev: The :data:`Event` to publish.
            fathom: Fathom adapter handle. Structural surface required:
                ``assert_with_provenance(template, slots, provenance)``.
        """
        try:
            self._send.send_nowait(ev)
            return
        except anyio.WouldBlock:
            if self._block_started_at is None:
                self._block_started_at = anyio.current_time()
            await self._send.send(ev)
            elapsed = anyio.current_time() - (self._block_started_at or 0.0)
            self._block_started_at = None
            if elapsed > self._backpressure_threshold_s:
                # Dispatch the sync Fathom assert through asyncio.to_thread
                # per design §3.7.2 verbatim so a slow CLIPS engine cannot
                # stall the event loop. Best-effort: a missing/half-wired
                # adapter must not crash the producer (FR-15 v1 contract:
                # "no drops, no overflow exceptions"). The block already
                # cleared (the send completed above), so swallowing an
                # emit failure does not lose any token.
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        fathom.assert_with_provenance,
                        "stargraph.evidence",
                        {
                            "kind": "stream-backpressure",
                            "buffer_used": self._max_buffer_size,
                            "max": self._max_buffer_size,
                            "block_seconds": elapsed,
                        },
                    )

    async def receive(self) -> Event:
        """Receive the next event (single-consumer per :class:`GraphRun`)."""
        return await self._recv.receive()

    async def aclose(self) -> None:
        """Close both endpoints (graceful shutdown)."""
        await self._send.aclose()
        await self._recv.aclose()

    async def __aenter__(self) -> EventBus:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
