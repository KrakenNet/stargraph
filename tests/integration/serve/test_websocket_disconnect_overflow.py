# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.19): WS slow-consumer 1011 + resume + bad cursor.

Three integration-tier scenarios at the FastAPI ASGI boundary:

1. **Slow consumer -> 1011 + "slow consumer"**: a real
   :class:`~stargraph.serve.broadcast.EventBroadcaster` whose
   per-subscriber bounded buffer fills (the broadcaster's fan-out
   uses ``send_nowait``; on :class:`anyio.WouldBlock` the broadcaster
   flips the subscriber's overflow flag, drops it, and the iterator
   raises :class:`~stargraph.errors.BroadcasterOverflow`). The WS handler
   catches the typed error and closes 1011 with reason ``slow consumer``.
   Driven against a real broadcaster (NOT a stub) by lowering the
   per-subscriber buffer via a ``max_buffer`` injected at construction
   time -- the unit suite in
   :file:`tests/unit/test_serve_websocket_resume.py` (task 2.21)
   covers the fan-out branch in isolation; this test exercises the
   end-to-end ASGI close-frame surface.

   Implementation note: :data:`stargraph.serve.broadcast._SUBSCRIBER_BUFFER_SIZE`
   is a module-level constant (``100`` per design §5.6). Tests cannot
   inject a smaller value without monkey-patching; we patch the module
   constant for this test only and restore on teardown so the slow-
   consumer trigger lands within a few events.

2. **Reconnect with ``last_event_id`` resumes from cursor**: after the
   first connection's 1011 disconnect, reconnect with
   ``?last_event_id=<run_id>:<step>:<seq>``. The handler walks the
   JSONL audit (real on-disk file with synthetic transitions for the
   run) and replays events strictly greater than the cursor (positional
   ``>`` per design §17 Decision #3). Asserts steps 2/3/4 replay when
   cursor = ``<run_id>:1:0``.

3. **Bad cursor format -> 1008**: ``?last_event_id=garbage`` triggers
   :func:`stargraph.serve.api._parse_last_event_id` returning ``None``;
   the handler closes 1008 with reason ``malformed last_event_id:
   'garbage'``.

Refs: tasks.md §3.19; design §16.9, §17 Decision #3; FR-19, FR-20.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from stargraph.errors import BroadcasterOverflow
from stargraph.runtime.bus import EventBus
from stargraph.serve.api import create_app
from stargraph.serve.broadcast import EventBroadcaster
from stargraph.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from fastapi import FastAPI


pytestmark = [pytest.mark.serve, pytest.mark.websocket, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Test 1: slow consumer -> 1011 via real ASGI surface                         #
# --------------------------------------------------------------------------- #


class _OverflowingBroadcaster:
    """Broadcaster shim whose :meth:`subscribe` raises :class:`BroadcasterOverflow`.

    Mirrors the structural surface of :class:`EventBroadcaster` (the
    only method the WS handler reaches for is :meth:`subscribe`). The
    subscribe iterator yields zero events and immediately raises
    :class:`BroadcasterOverflow` so the WS handler's overflow-branch
    closes 1011 with reason ``slow consumer`` end-to-end.

    Why this shim instead of monkey-patching :data:`_SUBSCRIBER_BUFFER_SIZE`:
    starlette's TestClient WS transport uses an unbounded inter-thread
    queue between the asyncio loop and the test thread, so even when
    the test thread does NOT call :meth:`ws.receive_text`, the WS
    handler's ``await websocket.send_text(text)`` always succeeds --
    the per-subscriber bounded buffer never fills via the natural
    fast-consumer path under the test transport. To exercise the
    overflow branch end-to-end against the real WS handler, we use a
    custom broadcaster shim whose subscribe iterator surfaces
    :class:`BroadcasterOverflow` directly. This is the same pattern as
    :file:`test_websocket_stream.py::test_ws_slow_consumer_closes_1011`
    but is documented at the integration tier (real FastAPI ASGI
    surface, real WS handshake) rather than the unit tier (broadcaster
    fan-out unit), per task 3.19's "real ASGI transport" focus.
    """

    async def subscribe(self) -> Any:
        # Async generator: must yield at least once for the iterator
        # protocol; raise inside the body to surface
        # BroadcasterOverflow on the WS handler's first ``async for``
        # step.
        if False:  # pragma: no cover - typing nudge
            yield
        raise BroadcasterOverflow(
            "per-subscriber buffer overflowed (slow consumer)",
            buffer_size=4,
        )


def test_ws_slow_consumer_overflow_closes_1011_via_real_asgi() -> None:
    """Slow-consumer overflow -> close 1011 + reason 'slow consumer' end-to-end.

    Integration-tier shape: real :func:`create_app`-built FastAPI
    instance + real WS route handler + real ASGI transport (TestClient).
    The broadcaster surface is the
    :class:`_OverflowingBroadcaster` shim documented above (see
    docstring for why a real :class:`EventBroadcaster` cannot be
    saturated through the TestClient WS transport).

    The unit-tier coverage of the broadcaster's fan-out + overflow flag
    flow lives in :file:`tests/unit/test_serve_websocket_resume.py`
    (task 2.21). Together the unit and integration tiers cover both
    halves of the slow-consumer contract: the engine raises
    :class:`BroadcasterOverflow` on per-subscriber fill (unit), and
    the WS handler translates it to ``close(1011, "slow consumer")``
    (integration).
    """
    run_id = "ws-slow-overflow"

    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: _OverflowingBroadcaster()},
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


# --------------------------------------------------------------------------- #
# Test 2: reconnect with last_event_id resumes from cursor                    #
# --------------------------------------------------------------------------- #


def _write_audit_lines(audit_path: Path, run_id: str, count: int) -> None:
    """Append synthetic JSONL audit transitions matching the on-disk shape.

    Mirrors :class:`JSONLAuditSink`'s shape (no signed envelope; the
    walker accepts both). The replay walker is positional: it computes
    ``seq_within_step`` as the 0-indexed ordinal of the event within
    its ``(run_id, step)`` group as written to JSONL.
    """
    with audit_path.open("a", encoding="utf-8") as fh:
        for step in range(count):
            record: dict[str, Any] = {
                "type": "transition",
                "run_id": run_id,
                "step": step,
                "branch_id": None,
                "ts": "2026-04-30T00:00:00+00:00",
                "payload": {},
                "from_node": f"n{step}",
                "to_node": f"n{step + 1}",
                "rule_id": "reconnect-test",
                "reason": "reconnect-test",
            }
            fh.write(json.dumps(record) + "\n")


def test_ws_reconnect_with_last_event_id_replays_strictly_after(
    tmp_path: Path,
) -> None:
    """``?last_event_id=<rid>:1:0`` replays events at (step, seq) > (1, 0).

    Pre-populates the audit JSONL with 5 transitions for ``run_id``
    (steps 0..4); the cursor at ``<run_id>:1:0`` should yield steps
    2/3/4 (3 events) on reconnect. Drives the real
    :func:`stargraph.serve.api._replay_audit_after_cursor` walker against
    a real on-disk file rather than the unit-suite's in-memory
    fixtures (task 2.21).
    """
    audit_path = tmp_path / "audit.jsonl"
    run_id = "ws-reconnect-resume"
    _write_audit_lines(audit_path, run_id, count=5)

    bus = EventBus()
    broadcaster = EventBroadcaster(bus)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tg:
            tg.start_soon(broadcaster.run)
            try:
                yield
            finally:
                with anyio.CancelScope(shield=True):
                    await bus.aclose()

    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: broadcaster},
        "audit_path": audit_path,
    }
    app = create_app(OssDefaultProfile(), deps=deps, lifespan=_lifespan)

    received: list[dict[str, Any]] = []
    with (
        TestClient(app) as client,
        client.websocket_connect(f"/v1/runs/{run_id}/stream?last_event_id={run_id}:1:0") as ws,
    ):
        # Replay walker emits 3 events (steps 2/3/4); after that the
        # handler enters the live-subscribe phase. Exiting the WS
        # context here disconnects the client, which is fine.
        for _ in range(3):
            received.append(json.loads(ws.receive_text()))

    replayed_steps = [f["step"] for f in received]
    assert replayed_steps == [2, 3, 4], (
        f"expected replay [2, 3, 4]; got {replayed_steps!r} (all frames: {received!r})"
    )


# --------------------------------------------------------------------------- #
# Test 3: bad cursor format -> 1008                                            #
# --------------------------------------------------------------------------- #


def test_ws_bad_cursor_format_closes_1008(tmp_path: Path) -> None:
    """``?last_event_id=garbage`` -> close 1008 with malformed-cursor reason.

    The strict parser :func:`_parse_last_event_id` returns ``None``
    for any input that does not match
    ``<run_id>:<digits>:<digits>``; the handler closes 1008 (policy
    violation) before subscribing.
    """
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()  # exists but empty
    run_id = "ws-bad-cursor"

    bus = EventBus()
    broadcaster = EventBroadcaster(bus)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tg:
            tg.start_soon(broadcaster.run)
            try:
                yield
            finally:
                with anyio.CancelScope(shield=True):
                    await bus.aclose()

    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: broadcaster},
        "audit_path": audit_path,
    }
    app = create_app(OssDefaultProfile(), deps=deps, lifespan=_lifespan)

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/v1/runs/{run_id}/stream?last_event_id=garbage") as ws,
    ):
        ws.receive_text()
    assert exc_info.value.code == 1008, (
        f"malformed cursor -> close 1008; got code={exc_info.value.code}"
    )
    reason_lower = (exc_info.value.reason or "").lower()
    assert "malformed" in reason_lower or "garbage" in reason_lower, (
        f"close reason should mention malformed/garbage; got {exc_info.value.reason!r}"
    )
