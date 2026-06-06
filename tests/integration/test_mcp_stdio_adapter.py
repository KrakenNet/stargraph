# SPDX-License-Identifier: Apache-2.0
"""FR-25 MCP stdio adapter integration tests (verbatim recipe).

Asserts the four behaviours required by ``requirements.md §FR-25`` and the
adapter recipe in ``design.md §3.3.2``:

1. ``bind(stdio_server, *, capabilities)`` returns a list of stargraph
   :class:`stargraph.ir.ToolSpec` objects, one per MCP tool reported by the
   session's ``list_tools()``.
2. Every ``call_tool`` validates ``arguments`` against the tool's MCP
   ``inputSchema`` (jsonschema draft 2020-12) *and* the returned payload
   against the expected ``outputSchema`` (FR-25 + design §3.3.2).
3. Tool outputs are sanitized -- HTML-escaped, control characters stripped,
   ``__system__`` markers removed -- before they reach any LM-context
   surface (FR-24 / AC-10.5).
4. Capability-gated: ``call_tool`` raises :class:`stargraph.errors.CapabilityError`
   when the tool's required permission is not granted by the
   :class:`stargraph.security.Capabilities` passed to ``bind`` (NFR-7).

This is the [TDD-RED] half of the seam: ``stargraph.adapters.mcp`` does not
yet exist (created in Task 3.6 [TDD-GREEN]), so importing it raises
``ImportError`` -- the verify gate ``grep -qE "(FAILED|ERROR)"`` matches that.
The tests use the in-memory :mod:`tests.fixtures.stub_mcp_server` instead of
a real subprocess; a Protocol-shaped session is the only contract the
adapter needs at the test boundary.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from stargraph.errors import CapabilityError
from stargraph.ir import ToolSpec
from stargraph.security import Capabilities, CapabilityClaim

# Load the in-memory stub MCP server from ``tests/fixtures/stub_mcp_server.py``.
# The ``tests`` directory is not a package (no ``__init__.py``), so we resolve
# the file path explicitly and load via ``importlib.util.spec_from_file_location``
# rather than relying on a ``from tests.fixtures...`` import path. This mirrors
# how pytest itself locates fixture modules without requiring a packaged tests
# tree.
_STUB_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "stub_mcp_server.py"
_stub_spec = importlib.util.spec_from_file_location(
    "_stub_mcp_server",
    _STUB_PATH,
)
assert _stub_spec is not None and _stub_spec.loader is not None
_stub_module = importlib.util.module_from_spec(_stub_spec)
sys.modules["_stub_mcp_server"] = _stub_module
_stub_spec.loader.exec_module(_stub_module)
StubMCPSession = _stub_module.StubMCPSession
echo_tool_spec = _stub_module.echo_tool_spec
restricted_tool_spec = _stub_module.restricted_tool_spec


@pytest.fixture
def mcp_adapter() -> Any:
    """Import the MCP adapter under test.

    Lives behind a fixture so collection succeeds even in [TDD-RED] state
    where ``stargraph.adapters.mcp`` is not yet implemented; per-test usage
    surfaces ``ModuleNotFoundError`` as a test failure (which the verify
    gate matches via ``grep -qE "(FAILED|ERROR)"``).
    """
    stargraph_mcp: Any = importlib.import_module("stargraph.adapters.mcp")
    return stargraph_mcp


@pytest.fixture
def open_capabilities() -> Capabilities:
    """A :class:`Capabilities` granting the ``fs.read:/secrets/*`` claim.

    Used to verify the *positive* capability path in cases 1-3 (so those
    tests don't accidentally trip the gate). Case 4 deliberately uses
    empty :class:`Capabilities` to assert the gate fires.
    """
    # ``CapabilityClaim`` is a frozen Pydantic model -> hashable at runtime,
    # but pyright's static checker doesn't always recognise that, so we
    # build the set explicitly via ``frozenset`` over a list literal.
    claim = CapabilityClaim(name="fs.read", scope="/secrets/*")
    return Capabilities(
        default_deny=False,
        granted={claim},  # pyright: ignore[reportUnhashable]
    )


def test_bind_returns_toolspecs_from_list_tools(
    mcp_adapter: Any,
    open_capabilities: Capabilities,
) -> None:
    """FR-25 case 1: ``bind`` -> ``list[ToolSpec]`` from ``list_tools``.

    Per design §3.3.2: the adapter opens a session, calls ``initialize()``
    then ``list_tools()``, and returns one :class:`ToolSpec` per MCP tool,
    translating MCP fields -> Stargraph fields (``inputSchema`` ->
    ``input_schema``, ``outputSchema`` -> ``output_schema``, etc.).
    """
    session = StubMCPSession(
        tools=[echo_tool_spec(), restricted_tool_spec()],
        responses={"echo": {"echoed": "hi"}},
    )

    specs: list[ToolSpec] = asyncio.run(
        mcp_adapter.bind(session, capabilities=open_capabilities),
    )

    assert isinstance(specs, list)
    assert len(specs) == 2
    assert {s.name for s in specs} == {"echo", "read-secret"}
    assert all(isinstance(s, ToolSpec) for s in specs)
    # The echo tool's input/output schemas should round-trip into the
    # Stargraph ToolSpec (adapter is a translator, not a transformer).
    echo = next(s for s in specs if s.name == "echo")
    assert echo.input_schema["type"] == "object"
    echo_in_props = echo.input_schema["properties"]
    assert isinstance(echo_in_props, dict) and "text" in echo_in_props
    assert echo.output_schema["type"] == "object"
    echo_out_props = echo.output_schema["properties"]
    assert isinstance(echo_out_props, dict) and "echoed" in echo_out_props
    # Session must have been ``initialize()``-d before ``list_tools()``.
    assert session.initialized is True


def test_call_tool_validates_input_and_output_schemas(
    mcp_adapter: Any,
    open_capabilities: Capabilities,
) -> None:
    """FR-25 case 2: ``call_tool`` validates input AND output schemas.

    The adapter must reject ``arguments`` that violate ``inputSchema``
    (jsonschema draft 2020-12) *before* invoking the session, and reject
    a session response that violates ``outputSchema`` *after* invoking.
    Both failures are surfaced as exceptions (concrete class is the
    [TDD-GREEN] task's choice; the contract is "loud, never silent").
    """
    session = StubMCPSession(
        tools=[echo_tool_spec()],
        responses={"echo": {"echoed": "ok"}},
    )

    specs: list[ToolSpec] = asyncio.run(
        mcp_adapter.bind(session, capabilities=open_capabilities),
    )
    echo = next(s for s in specs if s.name == "echo")

    # Input violates inputSchema (``text`` must be string, not int):
    with pytest.raises(Exception) as exc_in:
        asyncio.run(mcp_adapter.call_tool(session, echo, {"text": 123}))
    msg_in = str(exc_in.value).lower()
    assert "input" in msg_in or "schema" in msg_in or "valid" in msg_in

    # Now script a session response that violates outputSchema (wrong key):
    bad_session = StubMCPSession(
        tools=[echo_tool_spec()],
        responses={"echo": {"wrong_key": "boom"}},
    )
    with pytest.raises(Exception) as exc_out:
        asyncio.run(mcp_adapter.call_tool(bad_session, echo, {"text": "hello"}))
    msg_out = str(exc_out.value).lower()
    assert "output" in msg_out or "schema" in msg_out or "valid" in msg_out


def test_call_tool_sanitizes_outputs_before_lm_context(
    mcp_adapter: Any,
    open_capabilities: Capabilities,
) -> None:
    """FR-25 case 3: HTML-escape + control-char strip on output.

    Per design §3.3.2 + FR-24/AC-10.5: tool outputs are sanitized before
    any return-to-LM path. HTML-special chars (``<``, ``>``, ``&``) must
    be escaped; ASCII control chars (``\\x00`` - ``\\x1f`` excluding
    ``\\t \\n \\r``) must be stripped.
    """
    payload = "<script>alert('xss')</script>\x07hello\x1b[31mred"
    session = StubMCPSession(
        tools=[echo_tool_spec()],
        responses={"echo": {"echoed": payload}},
    )

    specs: list[ToolSpec] = asyncio.run(
        mcp_adapter.bind(session, capabilities=open_capabilities),
    )
    echo = next(s for s in specs if s.name == "echo")

    result: dict[str, Any] = asyncio.run(
        mcp_adapter.call_tool(session, echo, {"text": "hi"}),
    )

    cleaned = result["echoed"]
    assert isinstance(cleaned, str)
    # HTML escape: angle brackets + ampersand replaced.
    assert "<script>" not in cleaned
    assert "&lt;" in cleaned or "&#x3c;" in cleaned.lower()
    # Control chars stripped.
    assert "\x07" not in cleaned
    assert "\x1b" not in cleaned


def test_call_tool_capability_gated(mcp_adapter: Any) -> None:
    """FR-25 case 4: ``call_tool`` raises ``CapabilityError`` when not granted.

    Per NFR-7 + design §3.3.2: the adapter consults
    :class:`Capabilities` on every ``call_tool``. With empty capabilities,
    a tool whose ``ToolSpec.permissions`` list is non-empty must be
    refused -- the underlying ``session.call_tool`` is not invoked.
    """
    session = StubMCPSession(
        tools=[restricted_tool_spec()],
        responses={"read-secret": {"contents": "TOPSECRET"}},
    )
    empty_caps = Capabilities()  # no claims granted

    specs: list[ToolSpec] = asyncio.run(
        mcp_adapter.bind(session, capabilities=empty_caps),
    )
    restricted = next(s for s in specs if s.name == "read-secret")

    with pytest.raises(CapabilityError):
        asyncio.run(
            mcp_adapter.call_tool(session, restricted, {"path": "/secrets/key"}),
        )

    # The session's ``call_tool`` was never reached -- gate is in front.
    assert session.calls == []


def test_collect_mcp_adapters_aggregates_hookimpl_results(mcp_adapter: Any) -> None:
    """FR-25 plugin path: ``collect_mcp_adapters(pm)`` aggregates hookimpls.

    Plugin authors register MCP adapters under ``stargraph.mcp_adapters`` and
    implement ``register_mcp_adapters() -> list[MCPAdapterSpec]``.
    :func:`stargraph.adapters.mcp.collect_mcp_adapters` is the aggregator the
    serve / engine wiring drives at lifespan time. This test stands up a
    bare ``pluggy.PluginManager``, registers a fake plugin module, and
    asserts the collector returns the spec the plugin contributed.
    """
    import pluggy

    from stargraph.plugin import hookspecs
    from stargraph.plugin._markers import PROJECT
    from stargraph.plugin.types import MCPAdapterSpec

    pm = pluggy.PluginManager(PROJECT)
    pm.add_hookspecs(hookspecs)

    spec = MCPAdapterSpec(
        name="example-mcp",
        server=object(),  # session-shaped duck — adapter never opens it here
        required_capabilities=["mcp.example:read"],
    )

    class FakePlugin:
        @staticmethod
        @pluggy.HookimplMarker(PROJECT)
        def register_mcp_adapters() -> list[MCPAdapterSpec]:
            return [spec]

    pm.register(FakePlugin, name="fake-mcp-plugin")

    collected = mcp_adapter.collect_mcp_adapters(pm)
    assert collected == [spec]
