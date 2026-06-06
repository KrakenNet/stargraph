# SPDX-License-Identifier: Apache-2.0
"""Integration tests for :class:`stargraph.audit.JSONLAuditSink` (FR-22).

Coverage:
* ``test_basic_append`` -- unsigned three-event round-trip.
* ``test_signed_round_trip`` -- Ed25519-signed envelope verifies with
  the matching public key and rejects tampered payloads.
* ``test_append_only_invariant_no_seek_calls`` -- patches
  :func:`os.lseek` for the sink's lifetime and asserts zero seeks.
* ``test_rotation_size_based`` -- writes past a tiny ``max_bytes`` and
  asserts the active log stays under the cap while rotated siblings
  preserve full history.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import orjson
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from stargraph.audit import JSONLAuditSink
from stargraph.runtime.events import (
    Event,
    TokenEvent,
    ToolCallEvent,
    TransitionEvent,
)

if TYPE_CHECKING:
    from pathlib import Path


_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def _make_token_event(idx: int) -> TokenEvent:
    """Build a deterministic TokenEvent for fixture use."""
    return TokenEvent(
        run_id="run-1",
        step=idx,
        ts=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        model="gpt-4",
        token=f"tok-{idx}",
        index=idx,
    )


def test_basic_append(tmp_path: Path) -> None:
    """Three writes produce three JSONL lines that round-trip via Event."""
    log = tmp_path / "audit.jsonl"
    ts = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)

    events: list[Event] = [
        TokenEvent(
            run_id="run-1",
            step=0,
            ts=ts,
            model="gpt-4",
            token="hello",
            index=0,
        ),
        ToolCallEvent(
            run_id="run-1",
            step=1,
            ts=ts,
            tool_name="search",
            namespace="builtins",
            args={"q": "stargraph"},
            call_id="call-1",
        ),
        TransitionEvent(
            run_id="run-1",
            step=2,
            ts=ts,
            from_node="planner",
            to_node="executor",
            rule_id="r1",
            reason="plan-done",
        ),
    ]

    async def _run() -> None:
        sink = JSONLAuditSink(log)
        for ev in events:
            await sink.write(ev)
        await sink.close()

    asyncio.run(_run())

    raw_lines = log.read_bytes().splitlines()
    assert len(raw_lines) == 3, f"expected 3 lines, got {len(raw_lines)}"

    decoded = [_EVENT_ADAPTER.validate_python(orjson.loads(b)) for b in raw_lines]
    assert decoded[0].type == "token"
    assert decoded[1].type == "tool_call"
    assert decoded[2].type == "transition"
    # Round-trip equality on the originating events confirms ``mode="json"``
    # didn't drop any required fields.
    for original, parsed in zip(events, decoded, strict=True):
        assert parsed.model_dump(mode="json") == original.model_dump(mode="json")


def test_signed_round_trip(tmp_path: Path) -> None:
    """Signed records embed a hex Ed25519 signature that verifies with the public key."""
    log = tmp_path / "audit.jsonl"
    key = Ed25519PrivateKey.generate()
    public = key.public_key()
    events = [_make_token_event(i) for i in range(3)]

    async def _run() -> None:
        sink = JSONLAuditSink(log, signing_key=key)
        for ev in events:
            await sink.write(ev)
        await sink.close()

    asyncio.run(_run())

    raw_lines = log.read_bytes().splitlines()
    assert len(raw_lines) == 3

    for raw, original in zip(raw_lines, events, strict=True):
        envelope = orjson.loads(raw)
        # Envelope shape: {"event": {...}, "sig": "<hex>"}
        assert set(envelope.keys()) == {"event", "sig"}
        # Verifier re-encodes the inner event dict and checks the sig.
        public.verify(bytes.fromhex(envelope["sig"]), orjson.dumps(envelope["event"]))
        # Inner event still round-trips through the discriminated union.
        parsed = _EVENT_ADAPTER.validate_python(envelope["event"])
        assert parsed.model_dump(mode="json") == original.model_dump(mode="json")

    # Tamper detection: flipping a byte in the event payload must
    # invalidate the recorded signature.
    envelope = orjson.loads(raw_lines[0])
    tampered = dict(envelope["event"])
    tampered["token"] = "evil"
    with pytest.raises(InvalidSignature):
        public.verify(bytes.fromhex(envelope["sig"]), orjson.dumps(tampered))


def test_append_only_invariant_no_seek_calls(tmp_path: Path) -> None:
    """The sink must never call ``os.lseek`` -- writes go through ``O_APPEND`` only."""
    log = tmp_path / "audit.jsonl"
    events = [_make_token_event(i) for i in range(5)]

    real_lseek = os.lseek

    async def _run() -> None:
        sink = JSONLAuditSink(log)
        for ev in events:
            await sink.write(ev)
        await sink.close()

    with patch("os.lseek", wraps=real_lseek) as mock_lseek:
        asyncio.run(_run())
    assert mock_lseek.call_count == 0, "JSONLAuditSink leaked a seek-write path"


def test_rotation_size_based(tmp_path: Path) -> None:
    """Active log stays under ``max_bytes``; rotated siblings preserve history."""
    log = tmp_path / "audit.jsonl"
    # One TokenEvent serializes to ~150 bytes; pick a tiny cap so a few
    # writes force multiple rotations within the test runtime.
    cap = 300
    events = [_make_token_event(i) for i in range(8)]

    async def _run() -> None:
        sink = JSONLAuditSink(log, max_bytes=cap)
        for ev in events:
            await sink.write(ev)
        await sink.close()

    asyncio.run(_run())

    # Active log is under the cap.
    assert log.stat().st_size <= cap

    # Rotated siblings are <name>.0, <name>.1, ...; each also under cap.
    rotated = sorted(p for p in tmp_path.iterdir() if p.name.startswith("audit.jsonl."))
    assert rotated, "rotation never fired despite exceeding max_bytes"
    for sibling in rotated:
        assert sibling.stat().st_size <= cap

    # Concatenated history equals the full event sequence.
    all_lines: list[bytes] = []
    for sibling in rotated:
        all_lines.extend(sibling.read_bytes().splitlines())
    all_lines.extend(log.read_bytes().splitlines())

    assert len(all_lines) == len(events)
    decoded = [_EVENT_ADAPTER.validate_python(orjson.loads(b)) for b in all_lines]
    for original, parsed in zip(events, decoded, strict=True):
        assert parsed.model_dump(mode="json") == original.model_dump(mode="json")
