# SPDX-License-Identifier: Apache-2.0
"""Regression (#67): the broadcaster replays its pre-subscriber backlog.

A run that finishes before any WebSocket client (or event sink) subscribes
must not lose its events. The :class:`~stargraph.serve.broadcast.EventBroadcaster`
is the sole ``bus.receive()`` consumer; events it drains before the first
subscriber attaches would otherwise fan out to nobody and vanish -- fatal
for fast/trivial runs.

The fix retains drained-but-unwatched events in a bounded, drop-oldest
backlog and replays them to the first subscriber, then stops buffering
once live fan-out covers delivery. This test:

1. Drives ``broadcaster.run()`` with NO subscriber attached.
2. Emits ``N`` events onto the bus and waits until the broadcaster has
   drained all ``N`` into its pre-subscriber backlog.
3. Subscribes and asserts the first subscriber receives all ``N`` events
   in order -- proving none were dropped on the floor.

Refs: design §5.6 (broadcast wrapper), issue #67.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anyio
import anyio.lowlevel
import pytest

from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import TransitionEvent
from stargraph.serve.broadcast import EventBroadcaster

if TYPE_CHECKING:
    from stargraph.runtime.events import Event

pytestmark = [pytest.mark.serve, pytest.mark.integration]


_BACKLOG_EVENTS = 8


class _NullFathom:
    """Fathom shim for ``bus.send`` -- no policy emission."""

    def assert_with_provenance(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _transition(run_id: str, step: int) -> TransitionEvent:
    return TransitionEvent(
        run_id=run_id,
        step=step,
        ts=datetime.now(UTC),
        from_node=f"n{step}",
        to_node=f"n{step + 1}",
        rule_id="replay",
        reason="replay",
    )


async def test_broadcaster_replays_pre_subscriber_backlog() -> None:
    """Events drained before the first subscriber attaches are replayed (#67)."""
    run_id = "backlog-replay-run"
    bus = EventBus(max_buffer_size=256)
    broadcaster = EventBroadcaster(bus)
    received: list[Event] = []

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(broadcaster.run)

            # Emit N events with NO subscriber attached. The broadcaster's
            # consumer loop drains each into its bounded backlog.
            for step in range(_BACKLOG_EVENTS):
                await bus.send(_transition(run_id, step), fathom=_NullFathom())

            # Wait until all N are sitting in the pre-subscriber backlog --
            # this is the #67 path: drained from the bus, fanned out to
            # nobody, retained for replay rather than dropped.
            while len(broadcaster._backlog) < _BACKLOG_EVENTS:  # pyright: ignore[reportPrivateUsage]
                await anyio.lowlevel.checkpoint()
            assert broadcaster._had_subscriber is False  # pyright: ignore[reportPrivateUsage]

            # First subscriber attaches AFTER the run produced everything.
            async for ev in broadcaster.subscribe():
                received.append(ev)
                if len(received) >= _BACKLOG_EVENTS:
                    break

            tg.cancel_scope.cancel()

    # All N events replayed, in send order -- nothing lost to the
    # no-subscriber window.
    assert len(received) == _BACKLOG_EVENTS, (
        f"expected {_BACKLOG_EVENTS} replayed events; got {len(received)}"
    )
    steps = [ev.step for ev in received if isinstance(ev, TransitionEvent)]
    assert steps == list(range(_BACKLOG_EVENTS)), f"replay out of order: {steps!r}"
