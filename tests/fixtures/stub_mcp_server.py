# SPDX-License-Identifier: Apache-2.0
"""In-memory stub MCP server for FR-25 adapter integration tests.

The MCP stdio adapter (``stargraph.adapters.mcp.bind``, design §3.3.2) is built
against the official ``mcp.ClientSession`` interface (``initialize`` /
``list_tools`` / ``call_tool``). Standing up a real subprocess for each test
is heavy and flaky on CI; we instead provide a minimal in-memory stub that
mirrors the *shape* of the methods the adapter actually consumes, and let
the [TDD-GREEN] task (3.6) accept either via a small Protocol seam (or
via dependency injection of an already-opened session).

The stub is intentionally *not* a real `ClientSession` -- it just quacks
like one for the four FR-25 cases:

1. ``list_tools()`` -> returns a payload with ``tools: list[Tool]`` (each
   tool has ``name``, ``description``, ``inputSchema``, ``outputSchema``).
2. ``call_tool(name, arguments)`` -> returns a recorded result, or raises
   if the harness has scripted a failure.
3. The recorded results may include HTML / control-character payloads so
   the sanitizer test can assert post-sanitization output.

There is *no* real network or stdio IO. The stub exposes the surface as
plain ``async`` methods so an adapter wrapping a real ``ClientSession``
can swap to the stub without code change in the test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import TracebackType


@dataclass(frozen=True)
class StubTool:
    """Minimal mirror of the MCP ``Tool`` payload.

    Only the fields the adapter inspects are modelled. ``inputSchema`` and
    ``outputSchema`` are JSON-Schema draft 2020-12 dicts (validated by the
    adapter on every ``call_tool`` per FR-25 + design §3.3.2).
    """

    name: str
    description: str
    inputSchema: dict[str, Any]  # noqa: N815  -- MCP wire-name (camelCase)
    outputSchema: dict[str, Any]  # noqa: N815  -- MCP wire-name (camelCase)


@dataclass
class StubListToolsResult:
    """Mirror of ``mcp.types.ListToolsResult`` (shape: ``{tools: [Tool, ...]}``)."""

    tools: list[StubTool] = field(default_factory=list[StubTool])


@dataclass
class StubCallToolResult:
    """Mirror of ``mcp.types.CallToolResult``.

    Real MCP returns ``content: list[TextContent | ImageContent | ...]`` plus
    ``structuredContent`` (the typed JSON result). We only model the JSON
    output path here -- the sanitizer test asserts on the text payload.
    """

    structuredContent: dict[str, Any]  # noqa: N815  -- MCP wire-name (camelCase)
    isError: bool = False  # noqa: N815  -- MCP wire-name (camelCase)


class StubMCPSession:
    """In-memory stand-in for ``mcp.ClientSession`` (FR-25 test surface).

    The adapter under test (created in Task 3.6 [TDD-GREEN]) consumes the
    methods ``initialize``, ``list_tools``, ``call_tool``. We model just
    those three; the adapter is expected to accept any object that quacks
    like a ``ClientSession`` (Protocol-style).

    Use ``responses`` to script ``call_tool`` outputs by tool name. If a
    name is missing, ``call_tool`` raises ``KeyError`` (loud test failure).
    """

    def __init__(
        self,
        *,
        tools: list[StubTool] | None = None,
        responses: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._tools: list[StubTool] = list(tools or [])
        self._responses: dict[str, dict[str, Any]] = dict(responses or {})
        self.initialized: bool = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def initialize(self) -> None:
        """No-op stand-in for ``ClientSession.initialize``."""
        self.initialized = True

    async def list_tools(self) -> StubListToolsResult:
        """Return the scripted tool catalogue (mirrors ``ListToolsResult``)."""
        return StubListToolsResult(tools=list(self._tools))

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> StubCallToolResult:
        """Return a scripted ``CallToolResult`` for ``name`` with ``arguments``.

        The adapter is expected to validate ``arguments`` against the
        tool's ``inputSchema`` *before* calling this method, and the
        returned ``structuredContent`` against ``outputSchema`` *after*.
        Sanitization (HTML-escape + control-char strip) is applied to the
        post-validation output before any return-to-LM surface.
        """
        self.calls.append((name, dict(arguments)))
        if name not in self._responses:
            raise KeyError(f"stub mcp: no scripted response for tool {name!r}")
        return StubCallToolResult(structuredContent=dict(self._responses[name]))


def echo_tool_spec() -> StubTool:
    """A simple ``echo`` tool: ``{"text": str}`` -> ``{"echoed": str}``.

    Used by the FR-25 happy-path test (case 1) and the sanitization test
    (case 3) where the stub's scripted response contains HTML/control-char
    payloads the adapter must clean before returning to the LM context.
    """
    return StubTool(
        name="echo",
        description="Echo the input text back.",
        inputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        outputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
            "required": ["echoed"],
            "additionalProperties": False,
        },
    )


def restricted_tool_spec() -> StubTool:
    """A tool that requires a non-default capability ``fs.read:/secrets/*``.

    Used by the capability-gate test (case 4): an adapter ``call_tool``
    against this tool with empty ``Capabilities`` must raise
    ``CapabilityError`` *before* the underlying session is invoked.

    The required permission lives outside MCP's wire protocol; the
    adapter is expected to surface it via the ``ToolSpec.permissions``
    list when translating the MCP ``Tool`` -> Stargraph ``ToolSpec`` (the
    translation rule is part of the [TDD-GREEN] task 3.6 contract).
    """
    return StubTool(
        name="read-secret",
        description="Read a secret file.",
        inputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        outputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"contents": {"type": "string"}},
            "required": ["contents"],
            "additionalProperties": False,
        },
    )
