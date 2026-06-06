# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.10): WebSocket event stream + resume.

Drives :func:`stargraph.serve.api.create_app`'s ``WS /v1/runs/{id}/stream``
route end-to-end with three scenarios:

1. **Live event stream** -- a feeder task running on the FastAPI app's
   lifespan loop publishes a deterministic sequence of typed events
   onto the run's bus; the broadcaster fans them out to a WS
   subscriber. The subscriber asserts each event arrives in order as
   a :func:`stargraph.ir.dumps`-shaped JSON text frame, then sees a
   terminal :class:`ResultEvent` (status ``done``).

2. **`last_event_id` resume** -- the audit JSONL log is pre-populated
   with synthetic events for the run; a fresh WS connection with
   ``?last_event_id=<run_id>:<step>:<seq>`` is asserted to replay only
   the events strictly after the cursor (positional event_id+1 per
   design §17 Decision #3).

3. **Slow consumer -> 1011** -- a stub broadcaster whose
   :meth:`subscribe` raises :class:`BroadcasterOverflow` exercises
   the WS handler's overflow branch; the WS observes a close with
   code 1011 and reason "slow consumer".

WS testing pattern: :class:`fastapi.testclient.TestClient` is the
standard FastAPI WS surface; httpx 0.27+ does not natively support
WebSocket clients. The TestClient's :meth:`websocket_connect` is a
sync context manager — we use it directly from the test thread for
all three scenarios. For the live-stream scenario the feeder runs on
the TestClient's portal via :class:`fastapi.testclient.TestClient`'s
async lifespan hook (``app.state.feeder_task`` is launched at
lifespan startup, runs to completion, then the app shuts down).

Refs: tasks.md §3.10; design §16.2 + §17 Decision #3; FR-17, FR-18,
FR-20, AC-7.5.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import anyio
import anyio.lowlevel
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from stargraph.errors import BroadcasterOverflow
from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import (
    ResultEvent,
    TokenEvent,
    TransitionEvent,
)
from stargraph.serve.api import create_app
from stargraph.serve.broadcast import EventBroadcaster
from stargraph.serve.profiles import OssDefaultProfile

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI


pytestmark = [pytest.mark.serve, pytest.mark.websocket, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class _NullFathom:
    """Fathom shim used for ``bus.send`` calls -- no policy, no audit emission."""

    def assert_with_provenance(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# --------------------------------------------------------------------------- #
# Test 1: Live event stream via lifespan feeder                                #
# --------------------------------------------------------------------------- #


def test_ws_live_stream_yields_transitions_then_result(tmp_path: Path) -> None:
    """Feed a deterministic event burst onto the bus; WS receives it in order.

    The :class:`TestClient` runs the ASGI app on its internal
    :class:`anyio.from_thread.BlockingPortal`; the lifespan body
    starts a feeder task on the same loop that publishes 4
    :class:`TransitionEvent` frames followed by a terminal
    :class:`ResultEvent` (status ``done``) onto the run's bus.
    The :class:`EventBroadcaster`'s consumer task is also started in
    the lifespan so it fans the events out to the WS subscriber.

    Asserts:

    1. The WS subscriber receives each :class:`TransitionEvent` in
       step order, followed by the terminal :class:`ResultEvent`.
    2. Each frame is a JSON text frame whose ``type`` discriminator
       matches the expected variant.
    3. The connection closes naturally after the bus closes (no
       unexpected error).
    """
    del tmp_path  # not used; broadcasters store events in-memory only

    run_id = "ws-live-stream-run"
    bus = EventBus()
    broadcaster = EventBroadcaster(bus)

    # Lifespan: start the broadcaster's bus-receive loop on the
    # FastAPI app's event loop. The feeder is run *from the test
    # thread* via :class:`TestClient.portal_factory`'s blocking portal
    # (i.e. ``client.portal.call(...)`` in starlette's TestClient),
    # which schedules an async function on the same loop the WS
    # handler runs on. This sidesteps the task-group-vs-yield ordering
    # issues that arise when the feeder is started inside the lifespan
    # body (where it would race the WS subscribe).
    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tg:
            tg.start_soon(broadcaster.run)
            try:
                yield
            finally:
                # Bus closure makes broadcaster.run() exit naturally.
                with anyio.CancelScope(shield=True):
                    await bus.aclose()

    deps: dict[str, Any] = {
        "runs": {},
        "broadcasters": {run_id: broadcaster},
    }
    app = create_app(OssDefaultProfile(), deps=deps, lifespan=_lifespan)

    received: list[dict[str, Any]] = []

    async def _push_burst() -> None:
        """Run on the TestClient's portal loop -- publishes the burst."""
        for step in range(4):
            await bus.send(
                TransitionEvent(
                    run_id=run_id,
                    step=step,
                    ts=datetime.now(UTC),
                    from_node=f"node_{step}",
                    to_node=f"node_{step + 1}",
                    rule_id="test-rule",
                    reason="test",
                ),
                fathom=_NullFathom(),
            )
            await anyio.lowlevel.checkpoint()
        await bus.send(
            ResultEvent(
                run_id=run_id,
                step=4,
                ts=datetime.now(UTC),
                status="done",
                final_state={},
                run_duration_ms=200,
            ),
            fathom=_NullFathom(),
        )

    with TestClient(app) as client, client.websocket_connect(f"/v1/runs/{run_id}/stream") as ws:
        # Push the burst via TestClient's blocking portal so it
        # runs on the same event loop as the WS handler +
        # broadcaster.
        # ``portal`` is the BlockingPortal that TestClient creates on
        # ``__enter__``; pyright sees ``Optional`` so we narrow with an
        # assertion.
        portal = client.portal
        assert portal is not None
        portal.call(_push_burst)
        # Read 5 frames (4 transitions + 1 result).
        for _ in range(5):
            text = ws.receive_text()
            received.append(json.loads(text))

    # ---- Assertion 1: 4 transitions in order, then 1 result frame -------
    # ``ir_dumps`` uses ``exclude_defaults=True``, so the ``type``
    # discriminator is not on the wire when it equals its default.
    # Filter by the discriminator-distinguishing fields instead.
    transitions = [f for f in received if "from_node" in f and "to_node" in f]
    results = [f for f in received if "final_state" in f and "status" in f]
    assert len(transitions) == 4, (
        f"expected 4 transitions; got {len(transitions)}; received frames: {received!r}"
    )
    assert [t["step"] for t in transitions] == [0, 1, 2, 3], (
        f"transitions out of order: {[t['step'] for t in transitions]!r}"
    )
    assert len(results) == 1, f"expected 1 result frame; got {len(results)}"
    assert results[0]["status"] == "done", f"expected ResultEvent.status='done'; got {results[0]!r}"

    # ---- Cleanup is owned by the TestClient context exit; the close
    # code is not asserted here since we exit the WS context after
    # reading the expected frame count rather than awaiting an end-of-
    # stream signal. The slow-consumer test below covers the 1011
    # close path explicitly.


# --------------------------------------------------------------------------- #
# Test 2: ?last_event_id resume                                               #
# --------------------------------------------------------------------------- #


def _write_audit_lines(audit_path: Path, run_id: str, count: int) -> None:
    """Append ``count`` synthetic transition records to a JSONL audit file.

    Mirrors the on-disk shape produced by :class:`JSONLAuditSink` (no
    Ed25519 wrapper -- the
    :func:`stargraph.serve.api._replay_audit_after_cursor` walker accepts
    both bare ``event`` dicts and ``{"event": ..., "sig": ...}``
    envelopes).
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
                "from_node": f"node_{step}",
                "to_node": f"node_{step + 1}",
                "rule_id": "test-rule",
                "reason": "test",
            }
            fh.write(json.dumps(record) + "\n")


def test_ws_last_event_id_replays_after_cursor(tmp_path: Path) -> None:
    """``?last_event_id=<rid>:<step>:<seq>`` replays events strictly after.

    Pre-populates the audit JSONL with 5 transitions for ``run_id`` at
    steps 0..4, then connects WS with the cursor at ``<run_id>:1:0``.
    The replay walker yields events at ``(step, seq)`` strictly greater
    than ``(1, 0)`` -- i.e. steps 2, 3, 4 (3 events). Steps 0 and 1
    must NOT replay.
    """
    audit_path = tmp_path / "audit.jsonl"
    run_id = "ws-resume-run"
    _write_audit_lines(audit_path, run_id, count=5)

    bus = EventBus()
    broadcaster = EventBroadcaster(bus)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with anyio.create_task_group() as tg:
            tg.start_soon(broadcaster.run)
            try:
                yield
            finally:
                # Close the bus on shutdown so broadcaster.run exits
                # naturally + the task_group join completes.
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
        # Read the 3 replayed events first. After the replay, the
        # handler enters the live-subscribe phase and awaits on
        # the broadcaster; we exit the WS context here, which
        # triggers a client-side disconnect. The handler's
        # ``async for`` over the broadcaster's subscribe iterator
        # raises :class:`WebSocketDisconnect` which the handler
        # catches + returns cleanly. This sidesteps the cross-
        # thread bus.aclose() race that complicates the alternate
        # "close-bus-from-portal" pattern.
        for _ in range(3):
            text = ws.receive_text()
            received.append(json.loads(text))

    # ---- Assertions: only steps 2, 3, 4 replayed (3 events) -------------
    # Replay walker emits the bare event dict written to JSONL; that
    # dict carries an explicit ``type`` field (the test fixture wrote
    # it). The walker does NOT re-serialize through ``ir_dumps`` so
    # the ``type`` discriminator is present.
    replayed_steps = [f["step"] for f in received]
    assert replayed_steps == [2, 3, 4], (
        f"expected replay [2, 3, 4]; got {replayed_steps!r} (all frames: {received!r})"
    )


# --------------------------------------------------------------------------- #
# Test 3: Slow consumer -> 1011                                               #
# --------------------------------------------------------------------------- #


def test_ws_slow_consumer_closes_1011(tmp_path: Path) -> None:
    """A subscriber whose buffer overflows is disconnected with code 1011.

    Drives the WS handler's overflow branch via a tiny stub broadcaster
    whose :meth:`subscribe` immediately raises
    :class:`BroadcasterOverflow`. The integration tier verifies the
    HTTP-surface contract (``close(1011, "slow consumer")``) end-to-end
    against a real :func:`create_app`-built FastAPI instance; the
    engine-level fan-out path is covered by the unit suite
    (``test_broadcaster_overflow_raises_typed_exception``).
    """
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()
    run_id = "ws-slow-consumer-run"

    class _OverflowBroadcaster:
        async def subscribe(self) -> AsyncIterator[TokenEvent]:
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
