# SPDX-License-Identifier: Apache-2.0
"""WS ``last_event_id`` resume + 1011 disconnect-on-overflow (task 2.21).

Three behaviours under test:

1. **Resume cursor (positional)** -- design §17 Decision #3: when a client
   reconnects with ``?last_event_id=<run_id>:<step>:<seq_within_step>``,
   the WS handler replays the JSONL audit file forward from
   ``event_id+1`` (positional scan over the per-run log) before yielding
   live events. ``seq_within_step`` is the 0-indexed ordinal of the event
   within the ``(run_id, step)`` group as written to JSONL.

2. **Slow consumer disconnect** -- the broadcaster's per-subscriber
   bounded buffer (size 100 per design §5.6) must overflow into a
   distinct exception path so the WS handler can ``close(1011, "slow
   consumer")``. Awaiting a full buffer (the Phase-1 POC behaviour)
   is wrong: it stalls the broadcaster's main consumer loop and
   back-pressures every other subscriber.

3. **Strict cursor parsing** -- malformed ``last_event_id`` values
   (missing colons, non-digit step) close with ``1008`` (policy
   violation). Don't trust the client.

Tests use FastAPI's :class:`TestClient` (Starlette-backed) for WS;
the ``test_*`` functions are sync (TestClient runs the ASGI app in a
thread under the hood), and ``EventBroadcaster.run()`` is driven via
``anyio.from_thread.run_sync`` from the test thread when needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anyio
import anyio.lowlevel
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from stargraph.audit import JSONLAuditSink
from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import TokenEvent
from stargraph.serve.api import create_app
from stargraph.serve.broadcast import EventBroadcaster
from stargraph.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator as _AsyncIter
    from pathlib import Path


def _make_token_event(run_id: str, step: int, idx: int) -> TokenEvent:
    """Build a ``TokenEvent`` keyed at ``(run_id, step)`` -- ``idx`` for content."""
    return TokenEvent(
        run_id=run_id,
        step=step,
        ts=datetime.now(UTC),
        model="m",
        token=f"tok-{idx}",
        index=idx,
    )


async def _drain_sink_to_path(path: Path, events: list[TokenEvent]) -> None:
    """Write ``events`` to ``path`` via ``JSONLAuditSink`` (test-only helper)."""
    sink = JSONLAuditSink(path)
    try:
        for ev in events:
            await sink.write(ev)
    finally:
        await sink.close()


def test_ws_resume_replays_events_after_last_event_id(tmp_path: Path) -> None:
    """Reconnect with ``last_event_id=run-1:0:2`` -> client receives 0:3 + 0:4.

    Pre-seeds the audit JSONL with 5 events at step 0
    (``run-1:0:0`` ... ``run-1:0:4``), then connects WS with the
    cursor pointing at the third event. The handler must replay events
    with ordinal **strictly greater than** the cursor (positional scan
    forward) and then transition to live broadcaster events.
    """
    run_id = "run-1"
    audit_path = tmp_path / "audit.jsonl"

    events = [_make_token_event(run_id, step=0, idx=i) for i in range(5)]
    # Write events to JSONL using a sync helper that runs an asyncio loop.
    anyio.run(_drain_sink_to_path, audit_path, events)

    # Build broadcaster bound to a quiescent bus -- after audit replay
    # the WS pulls from the live broadcaster, but no live events are
    # published in this test (the post-replay close is what we assert).
    bus = EventBus()
    broadcaster = EventBroadcaster(bus)
    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: broadcaster},
        "audit_path": audit_path,
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/v1/runs/{run_id}/stream?last_event_id={run_id}:0:2") as ws,
    ):
        # Replay yields strictly-greater cursors: 0:3 and 0:4.
        line1 = ws.receive_text()
        line2 = ws.receive_text()
        payload1 = json.loads(line1)
        payload2 = json.loads(line2)
        assert payload1["index"] == 3, (
            f"first replayed event should be cursor 0:3 (index=3); got payload={payload1!r}"
        )
        assert payload2["index"] == 4, (
            f"second replayed event should be cursor 0:4 (index=4); got payload={payload2!r}"
        )


def test_ws_malformed_cursor_closes_1008(tmp_path: Path) -> None:
    """Malformed ``last_event_id`` (no colons) -> close 1008 policy violation."""
    run_id = "run-1"
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()  # empty file is fine

    bus = EventBus()
    broadcaster = EventBroadcaster(bus)
    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: broadcaster},
        "audit_path": audit_path,
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(
                f"/v1/runs/{run_id}/stream?last_event_id=not-a-valid-cursor"
            ) as ws,
        ):
            # Receive should immediately raise the close frame.
            ws.receive_text()
        assert exc_info.value.code == 1008, (
            f"malformed cursor should close 1008 (policy violation); got code={exc_info.value.code}"
        )


async def test_broadcaster_overflow_raises_typed_exception() -> None:
    """Per-subscriber buffer overflow surfaces ``BroadcasterOverflow`` (task 2.21).

    Drives the broadcaster's full fan-out path in-process:

    1. Build an :class:`EventBroadcaster` over a real :class:`EventBus`.
    2. Subscribe (allocates per-connection bounded stream of size 100).
    3. Slow consumer holds first event for 0.3s while producer emits
       past the buffer ceiling.
    4. The broadcaster's ``run()`` fan-out detects ``WouldBlock`` on the
       per-subscriber buffer, closes that send stream, and the
       subscriber's iterator drains buffered events then raises
       :class:`BroadcasterOverflow`.

    This is the engine-level overflow contract. The WS handler test
    (``test_ws_overflow_close_via_mock_broadcaster``) covers the HTTP
    surface translation (1011 close + "slow consumer" reason). Split
    intentionally: the WS test would require cross-loop event injection
    which TestClient's portal model does not cleanly support; the
    engine contract is verified end-to-end here, the HTTP-surface
    contract via the mock-broadcaster handler test.
    """
    from stargraph.errors import BroadcasterOverflow
    from stargraph.serve.broadcast import (
        _SUBSCRIBER_BUFFER_SIZE,  # pyright: ignore[reportPrivateUsage]
    )

    run_id = "run-overflow"
    bus = EventBus(max_buffer_size=_SUBSCRIBER_BUFFER_SIZE * 4)
    broadcaster = EventBroadcaster(bus)

    burst_size = _SUBSCRIBER_BUFFER_SIZE * 2 + 5  # well past overflow.

    received: list[int] = []
    overflowed: list[bool] = []

    async def _slow_subscriber() -> None:
        try:
            async for ev in broadcaster.subscribe():
                if isinstance(ev, TokenEvent):
                    received.append(ev.index)
                # Hold first event so the per-sub buffer fills behind us.
                if len(received) == 1:
                    await anyio.sleep(0.3)
        except BroadcasterOverflow:
            overflowed.append(True)

    async def _producer() -> None:
        # Poll until the subscriber has registered
        # (``broadcaster._subscribers`` populated). Without this the
        # producer drains the bus before the subscriber's first
        # __anext__ runs the registration body.
        for _ in range(100):
            if broadcaster._subscribers:  # pyright: ignore[reportPrivateUsage]
                break
            await anyio.sleep(0.01)
        for i in range(burst_size):
            ev = _make_token_event(run_id, step=0, idx=i)
            await _bus_send(bus, ev)
            # Yield so the broadcaster can fan this event out before the
            # next ``send`` -- the ``EventBus`` send_nowait path doesn't
            # yield by itself, so a tight loop blasts the bus and the
            # broadcaster never gets a turn.
            await anyio.lowlevel.checkpoint()
        # Let the broadcaster drain the bus (drains last events past the
        # overflow threshold) before closing the bus, so the overflow
        # path fires deterministically.
        await anyio.sleep(0.1)
        await bus.aclose()

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(broadcaster.run)
            tg.start_soon(_slow_subscriber)
            tg.start_soon(_producer)

    assert overflowed == [True], (
        f"slow subscriber should surface BroadcasterOverflow; "
        f"got overflowed={overflowed!r} received={len(received)}"
    )
    # Sanity: subscriber drained the buffered 100-ish events before
    # the iterator naturally ended on the closed send-side.
    assert len(received) >= _SUBSCRIBER_BUFFER_SIZE, (
        f"subscriber should drain buffered events before overflow surfaces; "
        f"received={len(received)}"
    )


def test_ws_overflow_close_via_mock_broadcaster(tmp_path: Path) -> None:
    """WS handler translates ``BroadcasterOverflow`` -> close(1011, "slow consumer").

    Drives only the WS handler's overflow branch via a tiny stub
    broadcaster whose ``subscribe()`` immediately raises
    :class:`BroadcasterOverflow`. Verifies the HTTP-surface contract
    (close code 1011 + reason "slow consumer") in isolation from the
    engine-level fan-out path tested in
    ``test_broadcaster_overflow_raises_typed_exception``.
    """
    from stargraph.errors import BroadcasterOverflow

    run_id = "run-stub"
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()

    class _OverflowBroadcaster:
        async def subscribe(self) -> _AsyncIter[TokenEvent]:
            # Generator must yield at least once for the iterator
            # protocol; raise inside the body to exercise the WS
            # handler's except branch.
            if False:
                yield  # pragma: no cover -- typing nudge
            raise BroadcasterOverflow(
                "per-subscriber buffer overflowed (slow consumer)",
                buffer_size=100,
            )

    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: _OverflowBroadcaster()},
        "audit_path": audit_path,
    }
    app = create_app(OssDefaultProfile(), deps=deps)

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/v1/runs/{run_id}/stream") as ws,
    ):
        ws.receive_text()
    assert exc_info.value.code == 1011, (
        f"BroadcasterOverflow -> close 1011; got code={exc_info.value.code}"
    )
    assert "slow consumer" in (exc_info.value.reason or "").lower(), (
        f"close reason should mention 'slow consumer'; got {exc_info.value.reason!r}"
    )


async def _bus_send(bus: EventBus, ev: TokenEvent) -> None:
    """Push one event onto the bus (best-effort fathom-less send)."""

    class _NullFathom:
        def assert_with_provenance(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    await bus.send(ev, fathom=_NullFathom())
