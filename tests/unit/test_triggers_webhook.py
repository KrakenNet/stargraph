# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``triggers/webhook._emit_audit`` structlog fallback (T16).

Pins that when ``self._audit_sink is None`` the method calls
``harbor.logging.get_logger(__name__).info("webhook_request", ...)`` with
the same :class:`BosunAuditEvent`-shaped payload rather than silently
dropping. The sink-present branch is NOT modified by T16.
"""

from __future__ import annotations

import pytest
import structlog

pytestmark = pytest.mark.unit


def _spec() -> object:
    """Build a minimal :class:`WebhookSpec`-shaped stand-in."""
    from harbor.triggers.webhook import WebhookSpec

    return WebhookSpec(
        trigger_id="t1",
        graph_id="g1",
        current_secret=b"secret",
        path="/hook",
    )


def _trigger_with_sink(sink: object | None) -> object:
    """Build a WebhookTrigger with the audit_sink wired."""
    from harbor.triggers.webhook import WebhookTrigger

    t = WebhookTrigger()
    t._audit_sink = sink  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    return t


@pytest.mark.unit
async def test_emit_audit_with_no_sink_logs_via_structlog() -> None:
    """``_emit_audit`` with ``self._audit_sink is None`` emits a
    ``webhook_request`` structlog event carrying ``BosunAuditEvent`` fields (T16)."""
    trigger = _trigger_with_sink(None)
    with structlog.testing.capture_logs() as logs:
        await trigger._emit_audit(  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportAttributeAccessIssue]
            spec=_spec(),
            kind="signature_invalid",
            detail={"reason": "bad-signature"},
        )
    assert any(entry.get("event") == "webhook_request" for entry in logs)


@pytest.mark.unit
async def test_emit_audit_with_sink_does_not_log_fallback() -> None:
    """When ``self._audit_sink`` is set, no structlog fallback fires (T16)."""

    class _CaptureSink:
        def __init__(self) -> None:
            self.events: list[object] = []

        async def write(self, ev: object) -> None:
            self.events.append(ev)

    sink = _CaptureSink()
    trigger = _trigger_with_sink(sink)
    with structlog.testing.capture_logs() as logs:
        await trigger._emit_audit(  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportAttributeAccessIssue]
            spec=_spec(),
            kind="signature_invalid",
            detail={"reason": "bad-signature"},
        )
    # The sink received the event; no structlog "webhook_request" fallback emitted.
    assert len(sink.events) == 1
    assert not any(entry.get("event") == "webhook_request" for entry in logs)
