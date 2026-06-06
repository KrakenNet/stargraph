# SPDX-License-Identifier: Apache-2.0
"""Integration tests for FR-21 / NFR-8 replay-safety enforcement.

Drives task 3.28 (TDD-GREEN): the engine must wire ``stargraph.replay.cassettes``
into the FR-24 tool-execution path so that tools with
``side_effects in {write, external}`` are stubbed, refused, or re-executed per
their ``replay_policy`` whenever ``run_ctx.is_replay`` is true.

The four cases below are pulled verbatim from design §3.4.4 step 3:

1. ``write`` + ``must_stub`` -> cassette payload returned, body never invoked.
2. ``external`` + ``fail_loud`` -> :class:`ReplayError`.
3. ``write`` + no explicit policy -> default policy is ``must_stub`` (FR-26).
4. ``read`` -> body re-executes natively even on replay.

The cassette-layer surface (``stargraph.replay.cassettes.ToolCallCassette``) is
the artifact task 3.28 ships; its absence is what flips this RED red.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest

from stargraph.errors import ReplayError
from stargraph.runtime.tool_exec import RunContext, execute_tool
from stargraph.security.capabilities import Capabilities, CapabilityClaim
from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_cassette() -> Any:
    """Return a fresh ``ToolCallCassette`` instance.

    Deferred-import (matches ``tests/integration/test_postgres_checkpointer.py``)
    so this RED file collects cleanly under ``pyright --strict`` until 3.28
    creates ``stargraph.replay.cassettes``.
    """
    mod = importlib.import_module(
        "stargraph.replay.cassettes",  # pyright: ignore[reportMissingImports]
    )
    cassette: Any = mod.ToolCallCassette()
    return cassette


# ---------------------------------------------------------------------------
# Tool fixtures with explicit invocation counters so we can assert
# "body never invoked" on the must-stub branch (Do #1).
# ---------------------------------------------------------------------------


_writer_calls: list[str] = []


@tool(
    name="writer_must_stub",
    namespace="test",
    version="1",
    side_effects=SideEffects.write,
    replay_policy=ReplayPolicy.must_stub,
    requires_capability="fs.write:/tmp/*",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    output_schema={
        "type": "object",
        "properties": {"written": {"type": "boolean"}},
        "required": ["written"],
    },
)
def writer_must_stub(path: str) -> dict[str, Any]:
    _writer_calls.append(path)
    return {"written": True}


@tool(
    name="external_fail_loud",
    namespace="test",
    version="1",
    side_effects=SideEffects.external,
    replay_policy=ReplayPolicy.fail_loud,
    input_schema={"type": "object"},
    output_schema={"type": "object"},
)
def external_fail_loud() -> dict[str, Any]:
    return {"ok": True}


@tool(
    name="writer_default",
    namespace="test",
    version="1",
    side_effects=SideEffects.write,
    requires_capability="fs.write:/tmp/*",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    output_schema={
        "type": "object",
        "properties": {"written": {"type": "boolean"}},
        "required": ["written"],
    },
)
def writer_default(path: str) -> dict[str, Any]:
    del path
    return {"written": True}


_reader_calls: list[str] = []


@tool(
    name="reader",
    namespace="test",
    version="1",
    side_effects=SideEffects.read,
    input_schema={
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
    output_schema={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
)
def reader_tool(key: str) -> dict[str, Any]:
    _reader_calls.append(key)
    return {"value": f"live:{key}"}


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_write_must_stub_returns_cassette_and_skips_body() -> None:
    """Do #1: write + must-stub -> cassette returned, body never invoked."""
    _writer_calls.clear()
    caps = Capabilities(granted={CapabilityClaim(name="fs.write", scope="/tmp/*")})  # pyright: ignore[reportUnhashable]
    cassette = _make_cassette()
    cassette.record("writer_must_stub", {"path": "/tmp/x"}, {"written": False})

    ctx = RunContext(
        run_id="r1",
        capabilities=caps,
        is_replay=True,
        cassette=cassette,
    )
    result = _run(execute_tool(writer_must_stub, {"path": "/tmp/x"}, run_ctx=ctx))
    assert result.replayed is True
    assert result.output == {"written": False}
    assert _writer_calls == [], "tool body must not be invoked under must_stub"


def test_external_fail_loud_raises_replay_error() -> None:
    """Do #2: external + fail-loud -> ReplayError on replay."""
    cassette = _make_cassette()
    cassette.record("external_fail_loud", {}, {"ok": True})  # cassette ignored
    ctx = RunContext(run_id="r1", is_replay=True, cassette=cassette)
    with pytest.raises(ReplayError):
        _run(execute_tool(external_fail_loud, {}, run_ctx=ctx))


def test_write_no_policy_defaults_to_must_stub() -> None:
    """Do #3: write side-effects + no replay_policy defaults to must-stub."""
    spec: Any = writer_default.spec  # type: ignore[attr-defined]
    assert spec.replay_policy == ReplayPolicy.must_stub

    caps = Capabilities(granted={CapabilityClaim(name="fs.write", scope="/tmp/*")})  # pyright: ignore[reportUnhashable]
    cassette = _make_cassette()
    cassette.record("writer_default", {"path": "/tmp/y"}, {"written": False})
    ctx = RunContext(
        run_id="r1",
        capabilities=caps,
        is_replay=True,
        cassette=cassette,
    )
    result = _run(execute_tool(writer_default, {"path": "/tmp/y"}, run_ctx=ctx))
    assert result.replayed is True
    assert result.output == {"written": False}


def test_read_side_effects_reexecute_natively_on_replay() -> None:
    """Do #4: read side-effects re-execute natively even when is_replay is true."""
    _reader_calls.clear()
    cassette = _make_cassette()
    cassette.record("reader", {"key": "k"}, {"value": "stub:k"})  # must NOT be used
    ctx = RunContext(
        run_id="r1",
        is_replay=True,
        cassette=cassette,
    )
    result = _run(execute_tool(reader_tool, {"key": "k"}, run_ctx=ctx))
    assert result.replayed is False
    assert result.output == {"value": "live:k"}
    assert _reader_calls == ["k"], "read tools must invoke their body on replay"
