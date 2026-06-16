# SPDX-License-Identifier: Apache-2.0
"""Broadcast wrapper over the single-consumer :class:`EventBus` (design §5.6).

Implements the :class:`EventBroadcaster` per design §5.6 + task 2.21:
one ``bus.receive()`` consumer per run, fanning events out to N
WebSocket subscribers via per-connection
``anyio.create_memory_object_stream(max_buffer_size=100)`` pairs.

* Constructor takes a connected :class:`stargraph.runtime.bus.EventBus` and
  is a pure data-holder -- no lifespan side effects (registry-friendly).
  The single consumer task is spawned only when the caller drives
  :meth:`run` from inside an existing ``anyio`` task group (typically
  the FastAPI lifespan).
* :meth:`subscribe` returns an ``AsyncIterator[Event]`` so route handlers
  can ``async for ev in broadcaster.subscribe(): ...`` directly. The
  iterator is backed by a per-connection bounded stream (size 100 per
  design §5.6) decoupling slow WS clients from the fast bus consumer.
* **Disconnect-on-overflow (task 2.21)**: when the broadcaster's
  fan-out cannot push to a subscriber's bounded stream non-blocking
  (``anyio.WouldBlock``), the subscriber is dropped, its send-side
  closed, and its iterator raises
  :class:`stargraph.errors.BroadcasterOverflow` so the WS handler can
  ``close(1011, "slow consumer")``. Replaces the Phase-1 POC behaviour
  of awaiting the per-subscriber stream (which back-pressured the
  broadcaster's main loop and stalled every other subscriber).
* Each subscriber has its own send/receive pair; teardown of one
  subscriber must not drop events for the others. The unsubscribe path
  uses ``anyio.CancelScope(shield=True)`` so a cancellation propagating
  from a disconnected client cannot tear down the broadcaster's main
  consumer loop.

Design refs: §5.6 (broadcast wrapper), §17 Decision #3 (disconnect-on-
overflow). FR-17, FR-18, NFR-2.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import anyio

from stargraph.errors import BroadcasterOverflow
from stargraph.runtime.events import Event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.streams.memory import (
        MemoryObjectReceiveStream,
        MemoryObjectSendStream,
    )

    from stargraph.runtime.bus import EventBus

__all__ = ["EventBroadcaster"]

#: Per-subscriber buffer size per design §5.6.
_SUBSCRIBER_BUFFER_SIZE = 100

#: Max events retained for startup replay before the first subscriber
#: attaches (#67). Bounded + drop-oldest so a run nobody ever watches
#: cannot grow the backlog without limit.
_BACKLOG_MAX = 1024


class _Subscriber:
    """Pair of ``(send, recv)`` streams plus an overflow flag.

    The broadcaster's main fan-out loop pushes to ``send`` non-blocking
    and flips ``overflowed`` if the buffer is full; the iterator on
    the receive side checks the flag on natural close to raise
    :class:`BroadcasterOverflow` instead of silently terminating.
    Module-private; not part of the public surface.
    """

    __slots__ = ("overflowed", "recv", "send")

    def __init__(
        self,
        send: MemoryObjectSendStream[Event],
        recv: MemoryObjectReceiveStream[Event],
    ) -> None:
        self.send = send
        self.recv = recv
        self.overflowed: bool = False


class EventBroadcaster:
    """Fan-out wrapper over a single-consumer :class:`EventBus`.

    The broadcaster is the sole ``bus.receive()`` consumer for a given
    run; it forwards each event to every active subscriber's bounded
    memory-object stream. Subscribers iterate their own receive end via
    :meth:`subscribe`; a slow subscriber is **dropped on overflow** --
    the broadcaster's fan-out uses ``send_nowait`` and on
    :class:`anyio.WouldBlock` flips the subscriber's overflow flag,
    closes its send stream, and removes it from the active set. The
    iterator then raises :class:`BroadcasterOverflow` on its next
    receive so the WS handler can issue ``close(1011, "slow consumer")``.

    Lifespan: the constructor does **not** start the consumer task --
    it stores the bus reference only (registry-friendly per
    Phase 2 lifespan wiring). Callers spawn :meth:`run` inside a task
    group; :meth:`aclose` (or task-group exit) tears the consumer down.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._subscribers: list[_Subscriber] = []
        self._closed = False
        # Startup replay backlog (#67): events the broadcaster reads from
        # the bus before any subscriber has attached would otherwise fan
        # out to nobody and be lost -- fatal for fast/trivial runs that
        # finish before a WS client or event sink subscribes. Retain them
        # (bounded, drop-oldest) until the first subscriber attaches, then
        # replay. Once any subscriber has attached, live fan-out covers
        # delivery and buffering stops.
        self._backlog: list[Event] = []
        self._had_subscriber = False

    async def run(self) -> None:
        """Drive the bus-receive loop until :meth:`aclose` or bus closure.

        Reads each event from the bus and fans it out to every active
        subscriber non-blocking. On any subscriber's full buffer
        (``anyio.WouldBlock``) the subscriber is flagged overflowed,
        its send stream is closed, and it is dropped from the active
        set; siblings continue to receive subsequent events. On bus
        closure (anyio raises :class:`anyio.EndOfStream` /
        :class:`anyio.ClosedResourceError`) the loop exits cleanly and
        closes all remaining subscriber send streams so their iterators
        terminate.
        """
        try:
            while not self._closed:
                try:
                    ev = await self._bus.receive()
                except (anyio.EndOfStream, anyio.ClosedResourceError):
                    break
                # Snapshot to tolerate concurrent subscribe/unsubscribe.
                for sub in list(self._subscribers):
                    try:
                        sub.send.send_nowait(ev)
                    except anyio.WouldBlock:
                        # Per-subscriber overflow -- drop this subscriber
                        # only; the iterator surfaces BroadcasterOverflow.
                        self._mark_overflow(sub)
                    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                        # Subscriber tore down its receive side; drop it.
                        self._drop_subscriber(sub)
                # Retain for startup replay until the first subscriber
                # attaches (#67). This whole post-receive block is
                # await-free, so it is atomic w.r.t. ``subscribe``'s
                # equally await-free attach prefix: an event is either
                # backlogged here or fanned out live above to a just-
                # attached subscriber -- never both, never neither.
                if not self._had_subscriber:
                    self._backlog.append(ev)
                    if len(self._backlog) > _BACKLOG_MAX:
                        del self._backlog[0]
        finally:
            await self._close_all_subscribers()

    async def subscribe(self) -> AsyncIterator[Event]:
        """Yield events as they arrive on the underlying bus.

        Each call allocates a fresh
        ``anyio.create_memory_object_stream(max_buffer_size=100)`` pair
        per design §5.6. The receive half drives the returned async
        iterator; the send half is registered with the broadcaster and
        removed on iterator teardown via a shielded cancel scope so a
        disconnecting client cannot cancel the broadcaster's main loop.

        Raises:
            BroadcasterOverflow: when the per-subscriber bounded buffer
                fills (slow consumer); the WS handler should translate
                this to ``close(1011, "slow consumer")`` per task 2.21.
        """
        send: MemoryObjectSendStream[Event]
        recv: MemoryObjectReceiveStream[Event]
        send, recv = anyio.create_memory_object_stream[Event](
            max_buffer_size=_SUBSCRIBER_BUFFER_SIZE
        )
        sub = _Subscriber(send, recv)
        self._subscribers.append(sub)
        # Startup replay (#67): the first subscriber drains any events the
        # broadcaster buffered before it attached. Snapshot + flip the flag
        # synchronously (no await between the append above and here, and the
        # run-loop's buffering block is also await-free) so a concurrently
        # arriving event is either in this backlog or fanned out live to
        # ``sub`` -- never duplicated, never dropped.
        replay: list[Event] = []
        if not self._had_subscriber:
            self._had_subscriber = True
            replay = self._backlog
            self._backlog = []
        try:
            for ev in replay:
                yield ev
            async for ev in recv:
                yield ev
            # Receive iterator ended naturally. If the broadcaster
            # closed our send-side because of overflow, surface that as
            # the typed error so the WS handler can disconnect 1011.
            if sub.overflowed:
                raise BroadcasterOverflow(
                    "per-subscriber buffer overflowed (slow consumer)",
                    buffer_size=_SUBSCRIBER_BUFFER_SIZE,
                )
        finally:
            # Shielded teardown: a cancellation propagating from the
            # WS handler must not tear down the broadcaster's consumer
            # loop or sibling subscribers (design §5.6).
            with anyio.CancelScope(shield=True):
                self._drop_subscriber(sub)
                await send.aclose()
                await recv.aclose()

    def _drop_subscriber(self, sub: _Subscriber) -> None:
        """Remove ``sub`` from the active subscriber list (idempotent)."""
        with contextlib.suppress(ValueError):
            self._subscribers.remove(sub)

    def _mark_overflow(self, sub: _Subscriber) -> None:
        """Flag ``sub`` as overflowed, close its send, and drop it.

        The receive-side iterator drains any already-buffered events and
        then sees end-of-stream; the closing block in :meth:`subscribe`
        consults the flag and raises :class:`BroadcasterOverflow`.
        """
        sub.overflowed = True
        self._drop_subscriber(sub)
        # Closing the send stream causes the receive iterator to end
        # naturally after draining buffered events. Best-effort: the
        # send may already be closed if the subscriber tore down
        # concurrently.
        with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
            sub.send.close()

    async def _close_all_subscribers(self) -> None:
        """Close every active subscriber send stream (consumer-loop exit)."""
        for sub in list(self._subscribers):
            with (
                anyio.CancelScope(shield=True),
                contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError),
            ):
                await sub.send.aclose()
        self._subscribers.clear()

    async def aclose(self) -> None:
        """Signal the consumer loop to stop and drop all subscribers."""
        self._closed = True
        await self._close_all_subscribers()
