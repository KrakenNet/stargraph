# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :class:`stargraph.triggers.webhook.WebhookTrigger` (FR-5, FR-9.1-9.5).

Covers the full HMAC-verification gauntlet:

* HMAC pass / tampered-body / tampered-signature.
* Nonce-replay (same nonce twice -> 409).
* Dual-secret rotation grace.
* Bad-timestamp window (replay defense).
* Clock-skew tolerance bonus.

Tests use an async-capable in-process Request stub instead of spinning
up FastAPI; the trigger's ``_handle_request`` operates on the
``request.body()`` + ``request.headers`` surface directly so a minimal
shim suffices.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import HTTPException

from stargraph.triggers.webhook import WebhookSpec, WebhookTrigger

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = [pytest.mark.unit, pytest.mark.trigger]


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                          #
# --------------------------------------------------------------------------- #


class _RecordingScheduler:
    """Captures :meth:`enqueue` calls so tests can assert on success path."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        graph_id: str,
        params: Mapping[str, Any],
        idempotency_key: str | None = None,
        *,
        trigger_source: str = "manual",
    ) -> Any:
        self.calls.append(
            {
                "graph_id": graph_id,
                "params": dict(params),
                "idempotency_key": idempotency_key,
                "trigger_source": trigger_source,
            }
        )
        return None


class _StubRequest:
    """Minimal :class:`fastapi.Request`-shaped stub for unit tests.

    Implements the two surfaces the trigger touches:

    * ``await request.body()`` -> raw bytes
    * ``request.headers`` -> dict-like (case-insensitive on the lookup
      side; the trigger normalises with ``k.lower()``).
    """

    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = body
        self.headers = headers

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def shared_secret() -> bytes:
    """Per-test ephemeral HMAC key (no hardcoded test secrets)."""
    return secrets.token_hex(32).encode()


@pytest.fixture
def previous_secret() -> bytes:
    """Per-test ephemeral rotation-grace key, distinct from the current secret."""
    return secrets.token_hex(32).encode()


def _make_trigger(
    spec: WebhookSpec, scheduler: _RecordingScheduler | None = None
) -> WebhookTrigger:
    sched = scheduler or _RecordingScheduler()
    trig = WebhookTrigger()
    trig.init({"scheduler": sched, "webhook_specs": [spec]})
    return trig


def _signed_request(*, secret: bytes, body: bytes, timestamp: int | None = None) -> _StubRequest:
    """Build a :class:`_StubRequest` with a valid HMAC signature.

    Uses the canonical Stripe-style signed-payload format (design §6.4).
    Returns the same shape :meth:`WebhookTrigger.sign` produces.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    sig = WebhookTrigger.sign(secret, ts, body)
    headers = {
        "x-stargraph-signature": sig,
        "x-stargraph-timestamp": str(ts),
    }
    return _StubRequest(body, headers)


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


async def test_hmac_pass_emits_enqueue(shared_secret: bytes) -> None:
    """Valid signature → trigger calls :meth:`Scheduler.enqueue` exactly once."""
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
    )
    sched = _RecordingScheduler()
    trig = _make_trigger(spec, sched)
    body = b'{"event": "ping"}'
    req = _signed_request(secret=shared_secret, body=body)
    result = await trig._handle_request(req, spec)  # pyright: ignore[reportPrivateUsage]
    assert result["accepted"] is True
    assert len(sched.calls) == 1
    expected_idem = WebhookTrigger.idempotency_key("webhook:t", body)
    assert sched.calls[0]["idempotency_key"] == expected_idem
    assert sched.calls[0]["params"] == {"event": "ping"}


async def test_hmac_fails_on_tampered_body(shared_secret: bytes) -> None:
    """Body tampered after signing → 401 ``invalid_signature``.

    Sign with one body, present the trigger with a different body and
    the original signature. The HMAC ties (timestamp, body) → digest;
    a body change breaks the digest match.
    """
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
    )
    trig = _make_trigger(spec)
    ts = int(time.time())
    sig = WebhookTrigger.sign(shared_secret, ts, b'{"event":"ping"}')
    tampered_body = b'{"event":"DOOM"}'
    req = _StubRequest(
        tampered_body,
        {"x-stargraph-signature": sig, "x-stargraph-timestamp": str(ts)},
    )
    with pytest.raises(HTTPException) as excinfo:
        await trig._handle_request(req, spec)  # pyright: ignore[reportPrivateUsage]
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_signature"


async def test_hmac_fails_on_tampered_signature(shared_secret: bytes) -> None:
    """Signature tampered → 401 ``invalid_signature`` (constant-time compare)."""
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
    )
    trig = _make_trigger(spec)
    body = b'{"event":"ping"}'
    ts = int(time.time())
    real_sig = WebhookTrigger.sign(shared_secret, ts, body)
    # Flip one nibble of the hex digest; HMAC must reject.
    tampered_sig = ("0" if real_sig[0] != "0" else "1") + real_sig[1:]
    req = _StubRequest(
        body,
        {"x-stargraph-signature": tampered_sig, "x-stargraph-timestamp": str(ts)},
    )
    with pytest.raises(HTTPException) as excinfo:
        await trig._handle_request(req, spec)  # pyright: ignore[reportPrivateUsage]
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_signature"


async def test_nonce_replay_rejects_second_send(shared_secret: bytes) -> None:
    """Replaying the same (signature, timestamp) → 409 ``duplicate_nonce``.

    First request inside the window passes; second identical request
    must hit the LRU and 409. Tests the FR-9.3 nonce-defense.
    """
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
    )
    sched = _RecordingScheduler()
    trig = _make_trigger(spec, sched)
    body = b'{"event":"replay"}'
    ts = int(time.time())
    sig = WebhookTrigger.sign(shared_secret, ts, body)
    headers = {"x-stargraph-signature": sig, "x-stargraph-timestamp": str(ts)}

    # First send: passes.
    first = await trig._handle_request(_StubRequest(body, headers), spec)  # pyright: ignore[reportPrivateUsage]
    assert first["accepted"] is True
    assert len(sched.calls) == 1
    # Second send: same triple (trigger_id, sig, ts) → 409.
    with pytest.raises(HTTPException) as excinfo:
        await trig._handle_request(_StubRequest(body, headers), spec)  # pyright: ignore[reportPrivateUsage]
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "duplicate_nonce"
    # Scheduler did NOT receive the second enqueue.
    assert len(sched.calls) == 1


async def test_dual_secret_rotation_grace(shared_secret: bytes, previous_secret: bytes) -> None:
    """Body signed with ``previous_secret`` is accepted (rotation grace).

    The 90-day rotation grace (Resolved Decision #8) keeps the previous
    key live for verification only, so in-flight callers do not 401 on
    the rotation seam.
    """
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        previous_secret=previous_secret,
        graph_id="graph-webhook",
    )
    sched = _RecordingScheduler()
    trig = _make_trigger(spec, sched)
    body = b'{"event":"rotation"}'
    # Sign with previous_secret (the "old key still in flight" case).
    req = _signed_request(secret=previous_secret, body=body)
    result = await trig._handle_request(req, spec)  # pyright: ignore[reportPrivateUsage]
    assert result["accepted"] is True
    assert len(sched.calls) == 1


async def test_bad_timestamp_outside_window_rejected(shared_secret: bytes) -> None:
    """Timestamp older than ``timestamp_window_seconds`` → 401 ``timestamp_out_of_window``.

    Replay-window defense: the HMAC alone cannot stop an attacker who
    captured a valid signature; the timestamp window forces the reuse
    window to ≤ 5 minutes by default.
    """
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
        timestamp_window_seconds=300,
    )
    trig = _make_trigger(spec)
    body = b'{"event":"old"}'
    # Sign with a timestamp 1 hour in the past -> outside the 5-min window.
    stale_ts = int(time.time()) - 3600
    req = _signed_request(secret=shared_secret, body=body, timestamp=stale_ts)
    with pytest.raises(HTTPException) as excinfo:
        await trig._handle_request(req, spec)  # pyright: ignore[reportPrivateUsage]
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "timestamp_out_of_window"


async def test_clock_skew_tolerated_within_window(shared_secret: bytes) -> None:
    """Slight future timestamp (within ±window) is accepted (clock-skew tolerance).

    A timestamp 10 seconds in the future stays well inside the
    300-second window; accepted. A timestamp 10 minutes in the future
    is outside the window; rejected.
    """
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
        timestamp_window_seconds=300,
    )
    sched = _RecordingScheduler()
    trig = _make_trigger(spec, sched)
    body_ok = b'{"event":"future-ok"}'
    body_far = b'{"event":"future-far"}'
    # Within window: 10s ahead.
    req_ok = _signed_request(secret=shared_secret, body=body_ok, timestamp=int(time.time()) + 10)
    result = await trig._handle_request(req_ok, spec)  # pyright: ignore[reportPrivateUsage]
    assert result["accepted"] is True
    # Outside window: 10min ahead.
    req_far = _signed_request(secret=shared_secret, body=body_far, timestamp=int(time.time()) + 600)
    with pytest.raises(HTTPException) as excinfo:
        await trig._handle_request(req_far, spec)  # pyright: ignore[reportPrivateUsage]
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "timestamp_out_of_window"


async def test_missing_headers_rejected(shared_secret: bytes) -> None:
    """Missing signature or timestamp header → 401 ``missing_headers``."""
    spec = WebhookSpec(
        trigger_id="webhook:t",
        path="/v1/webhooks/test",
        current_secret=shared_secret,
        graph_id="graph-webhook",
    )
    trig = _make_trigger(spec)
    req = _StubRequest(b'{"event":"x"}', {})  # neither header
    with pytest.raises(HTTPException) as excinfo:
        await trig._handle_request(req, spec)  # pyright: ignore[reportPrivateUsage]
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "missing_headers"


def test_verify_static_helper_constant_time(shared_secret: bytes) -> None:
    """:meth:`WebhookTrigger.verify` returns False on bad sig, True on good."""
    body = b'{"event":"verify"}'
    ts = int(time.time())
    good_sig = WebhookTrigger.sign(shared_secret, ts, body)
    bad_sig = hmac.new(b"wrong-key", b"x", hashlib.sha256).hexdigest()
    assert WebhookTrigger.verify(
        current_secret=shared_secret,
        previous_secret=None,
        timestamp=ts,
        raw_body=body,
        signature=good_sig,
    )
    assert not WebhookTrigger.verify(
        current_secret=shared_secret,
        previous_secret=None,
        timestamp=ts,
        raw_body=body,
        signature=bad_sig,
    )
