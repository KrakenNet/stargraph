# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the FR-24 / design §3.4.4 tool execution pipeline.

Each test exercises one of the nine pipeline steps with a stub tool +
stub run-context. Real :class:`fathom.Engine` and DSPy/MCP stacks are
out of scope here -- the pipeline must be unit-testable on its own.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stargraph.errors import (
    CapabilityError,
    IRValidationError,
    ReplayError,
)
from stargraph.runtime.tool_exec import (
    CassetteStore,
    RunContext,
    execute_tool,
)
from stargraph.security.capabilities import Capabilities, CapabilityClaim
from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects

# ---------------------------------------------------------------------------
# Stub fixtures.
# ---------------------------------------------------------------------------


class _RecordingFathom:
    """Minimal stand-in for FathomAdapter -- records assert_with_provenance calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: dict[str, Any],
    ) -> None:
        del provenance  # not asserted directly; provenance shape covered elsewhere
        self.calls.append((template, dict(slots)))


class _DictCassette:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._payload = payload

    def get(self, tool_id: str, args: dict[str, Any]) -> dict[str, Any] | None:
        del tool_id, args
        return self._payload


# ---------------------------------------------------------------------------
# Tools used across multiple tests.
# ---------------------------------------------------------------------------


@tool(
    name="echo",
    namespace="test",
    version="1",
    side_effects=SideEffects.none,
    input_schema={
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    },
    output_schema={
        "type": "object",
        "properties": {"echoed": {"type": "string"}},
        "required": ["echoed"],
    },
)
def echo_tool(msg: str) -> dict[str, Any]:
    return {"echoed": msg}


@tool(
    name="writer",
    namespace="test",
    version="1",
    side_effects=SideEffects.write,
    requires_capability="fs.write:/data/*",
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
def writer_tool(path: str) -> dict[str, Any]:
    del path
    return {"written": True}


@tool(
    name="external_loud",
    namespace="test",
    version="1",
    side_effects=SideEffects.external,
    replay_policy=ReplayPolicy.fail_loud,
    input_schema={"type": "object"},
    output_schema={"type": "object"},
)
def external_loud_tool() -> dict[str, Any]:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tests, one per pipeline step (plus output-shape edge cases).
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_step1_input_schema_validation_rejects_bad_args() -> None:
    """Step 1: invalid args raise IRValidationError before any other gate."""
    ctx = RunContext(run_id="r1")
    with pytest.raises(IRValidationError) as excinfo:
        _run(execute_tool(echo_tool, {"msg": 42}, run_ctx=ctx))
    assert excinfo.value.context["violation"] == "tool-input-schema"


def test_step2_capability_default_deny_blocks_writer() -> None:
    """Step 2: tool with required permission + no grant raises CapabilityError."""
    ctx = RunContext(run_id="r1")  # no capabilities wired
    with pytest.raises(CapabilityError) as excinfo:
        _run(execute_tool(writer_tool, {"path": "/data/x"}, run_ctx=ctx))
    assert excinfo.value.context["tool_id"] == "writer"


def test_step2_capability_granted_allows_invocation() -> None:
    """Step 2: explicit grant satisfying the scope passes through."""
    caps = Capabilities(
        granted={CapabilityClaim(name="fs.write", scope="/data/*")},  # pyright: ignore[reportUnhashable]
    )
    ctx = RunContext(run_id="r1", capabilities=caps)
    result = _run(execute_tool(writer_tool, {"path": "/data/x"}, run_ctx=ctx))
    assert result.output == {"written": True}
    assert result.replayed is False


def test_step3_replay_must_stub_returns_cassette() -> None:
    """Step 3: write side-effect under must_stub returns the cassette payload."""
    caps = Capabilities(granted={CapabilityClaim(name="fs.write", scope="/data/*")})  # pyright: ignore[reportUnhashable]
    cassette = _DictCassette({"written": False})
    ctx = RunContext(
        run_id="r1",
        capabilities=caps,
        is_replay=True,
        cassette=cassette,
    )
    result = _run(execute_tool(writer_tool, {"path": "/data/x"}, run_ctx=ctx))
    assert result.replayed is True
    assert result.output == {"written": False}


def test_step3_replay_must_stub_missing_cassette_raises() -> None:
    """Step 3: must_stub policy + no cassette entry raises ReplayError."""
    caps = Capabilities(granted={CapabilityClaim(name="fs.write", scope="/data/*")})  # pyright: ignore[reportUnhashable]
    ctx = RunContext(
        run_id="r1",
        capabilities=caps,
        is_replay=True,
        cassette=_DictCassette(None),
    )
    with pytest.raises(ReplayError) as excinfo:
        _run(execute_tool(writer_tool, {"path": "/data/x"}, run_ctx=ctx))
    assert excinfo.value.context["tool_id"] == "writer"


def test_step3_replay_fail_loud_always_raises() -> None:
    """Step 3: fail_loud policy raises regardless of cassette presence."""
    ctx = RunContext(
        run_id="r1",
        is_replay=True,
        cassette=_DictCassette({"ok": True}),  # ignored
    )
    with pytest.raises(ReplayError):
        _run(execute_tool(external_loud_tool, {}, run_ctx=ctx))


def test_step4_and_step8_emit_call_and_result_facts() -> None:
    """Steps 4 + 8: both stargraph.tool-call and stargraph.tool-result emit via Fathom."""
    fathom = _RecordingFathom()
    ctx = RunContext(run_id="r1", fathom=fathom)  # type: ignore[arg-type]
    _run(execute_tool(echo_tool, {"msg": "hi"}, run_ctx=ctx))
    templates = [c[0] for c in fathom.calls]
    assert "stargraph.tool-call" in templates
    assert "stargraph.tool-result" in templates
    # Order matters: call before invocation, result after.
    assert templates.index("stargraph.tool-call") < templates.index("stargraph.tool-result")


def test_step5_invokes_tool_body_when_not_replayed() -> None:
    """Step 5: live mode actually calls the tool body."""
    invoked: list[str] = []

    @tool(
        name="probe",
        namespace="test",
        version="1",
        side_effects=SideEffects.none,
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    )
    def probe() -> dict[str, Any]:
        invoked.append("hit")
        return {"n": 1}

    ctx = RunContext(run_id="r1")
    result = _run(execute_tool(probe, {}, run_ctx=ctx))
    assert invoked == ["hit"]
    assert result.output == {"n": 1}


def test_step6_output_schema_failure_raises() -> None:
    """Step 6: output that violates output_schema raises IRValidationError."""

    @tool(
        name="bad_out",
        namespace="test",
        version="1",
        side_effects=SideEffects.none,
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        },
    )
    def bad_out() -> dict[str, Any]:
        return {"n": "not-an-int"}

    ctx = RunContext(run_id="r1")
    with pytest.raises(IRValidationError) as excinfo:
        _run(execute_tool(bad_out, {}, run_ctx=ctx))
    assert excinfo.value.context["violation"] == "tool-output-schema"


def test_step7_sanitization_strips_control_chars_and_markers() -> None:
    """Step 7: HTML-escape + control-char strip + marker removal applied."""

    @tool(
        name="dirty",
        namespace="test",
        version="1",
        side_effects=SideEffects.none,
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    def dirty() -> dict[str, Any]:
        return {"text": "hi\x1bthere<script>__system__"}

    ctx = RunContext(run_id="r1")
    result = _run(execute_tool(dirty, {}, run_ctx=ctx))
    text = result.output["text"]
    assert "\x1b" not in text  # control char stripped
    assert "<script>" not in text  # HTML-escaped
    assert "__system__" not in text  # marker removed


def test_step9_emits_tokens_used_when_reported() -> None:
    """Step 9: tools that report `_tokens` produce a stargraph.tokens-used fact."""

    @tool(
        name="lm_call",
        namespace="test",
        version="1",
        side_effects=SideEffects.none,
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "_tokens": {"type": "object"},
            },
            "required": ["answer"],
        },
    )
    def lm_call() -> dict[str, Any]:
        return {"answer": "42", "_tokens": {"prompt": 10, "completion": 5, "total": 15}}

    fathom = _RecordingFathom()
    ctx = RunContext(run_id="r1", fathom=fathom)  # type: ignore[arg-type]
    result = _run(execute_tool(lm_call, {}, run_ctx=ctx))
    templates = [c[0] for c in fathom.calls]
    assert "stargraph.tokens-used" in templates
    assert result.tokens == {"prompt": 10, "completion": 5, "total": 15}


def test_step9_skipped_when_no_tokens_reported() -> None:
    """Step 9: pipeline does NOT emit tokens-used when _tokens is absent."""
    fathom = _RecordingFathom()
    ctx = RunContext(run_id="r1", fathom=fathom)  # type: ignore[arg-type]
    _run(execute_tool(echo_tool, {"msg": "hi"}, run_ctx=ctx))
    templates = [c[0] for c in fathom.calls]
    assert "stargraph.tokens-used" not in templates


def test_async_tool_body_is_awaited() -> None:
    """Step 5: async tool callables are awaited transparently."""

    @tool(
        name="async_probe",
        namespace="test",
        version="1",
        side_effects=SideEffects.none,
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    )
    async def async_probe() -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"n": 7}

    ctx = RunContext(run_id="r1")
    result = _run(execute_tool(async_probe, {}, run_ctx=ctx))
    assert result.output == {"n": 7}


def test_cassette_store_protocol_is_runtime_checkable() -> None:
    """The CassetteStore Protocol allows duck-typed implementations."""
    assert isinstance(_DictCassette({"x": 1}), CassetteStore)
