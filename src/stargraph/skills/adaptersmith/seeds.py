# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed adapters — the trainset cold start.

The single seed is the full MCP functional seam (a port of
``stargraph.adapters.mcp``): module-level async ``bind`` + ``call_tool`` plus the
translate / validate / sanitize helpers and the tool-name → required-permissions
map. Its ``test_adapter.py`` exercises translation, input/output schema
validation, output sanitization, and the capability gate against an in-test
session stub. It gives RAG retrieval and few-shot compile something to stand on
before the generator has produced anything. ``id`` is a fixed literal so
``seed_trainset`` is idempotent across runs.

``tests/integration/adaptersmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any


def _pair(
    seed_id: str,
    brief: str,
    adapter_name: str,
    namespace: str,
    fixture: dict[str, Any],
    adapter_source: str,
    test_source: str,
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "adapter_name": adapter_name,
        "namespace": namespace,
        "fixture": fixture,
        "adapter_source": adapter_source,
        "test_source": test_source,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


_MCP_ADAPTER_SOURCE = '''\
"""MCP functional seam: translate an MCP tool catalogue into Stargraph ToolSpecs
and gate every call_tool through capability check -> input-validate -> invoke ->
output-validate -> sanitize. The real ``mcp`` package is lazy-imported only on
the stdio transport branch, so the module is importable offline."""

from __future__ import annotations

import html
import re
from typing import Any, Protocol, runtime_checkable

from jsonschema import Draft202012Validator

from stargraph.errors import CapabilityError, IRValidationError
from stargraph.ir import ToolSpec
from stargraph.security import Capabilities
from stargraph.tools.spec import ReplayPolicy, SideEffects

_SESSION_CAPS: dict[int, Capabilities] = {}

_CONTROL_CHARS_RE = re.compile(r"[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]")
_SYSTEM_MARKER_RE = re.compile(r"__system__|<\\|im_start\\|>|<\\|im_end\\|>", re.IGNORECASE)

_TOOL_PERMISSIONS: dict[str, list[str]] = {
    "read-secret": ["fs.read:/secrets/*"],
}


@runtime_checkable
class _MCPSessionLike(Protocol):
    async def initialize(self) -> Any: ...
    async def list_tools(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _sanitize_str(value: str) -> str:
    stripped = _CONTROL_CHARS_RE.sub("", value)
    escaped = html.escape(stripped, quote=True)
    return _SYSTEM_MARKER_RE.sub("", escaped)


def _sanitize(value: object) -> object:
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _tool_attr(tool: object, name: str) -> Any:
    if isinstance(tool, dict):
        return tool.get(name)
    return getattr(tool, name, None)


def _required_permissions(tool_name: str) -> list[str]:
    return list(_TOOL_PERMISSIONS.get(tool_name, []))


def _translate(tool: object) -> ToolSpec:
    name = _tool_attr(tool, "name")
    description = _tool_attr(tool, "description") or ""
    raw_input: object = _tool_attr(tool, "inputSchema") or {}
    raw_output: object = _tool_attr(tool, "outputSchema") or {}
    if not isinstance(name, str):
        raise IRValidationError("MCP tool missing a string name", violation="mcp-tool-missing-name")
    if not isinstance(raw_input, dict) or not isinstance(raw_output, dict):
        raise IRValidationError(f"MCP tool {name!r} has non-dict schema(s)", tool_id=name)
    input_schema: dict[str, object] = {str(k): v for k, v in raw_input.items()}
    output_schema: dict[str, object] = {str(k): v for k, v in raw_output.items()}
    return ToolSpec(
        name=name,
        namespace="mcp",
        version="1",
        description=str(description),
        input_schema=input_schema,
        output_schema=output_schema,
        side_effects=SideEffects.external,
        replay_policy=ReplayPolicy.must_stub,
        permissions=_required_permissions(name),
    )


def _extract_tools(result: object) -> list[object]:
    if isinstance(result, dict):
        tools = result.get("tools", [])
    else:
        tools = getattr(result, "tools", [])
    if not isinstance(tools, list):
        return []
    return list(tools)


def _extract_structured(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        candidate = raw.get("structuredContent", raw)
    else:
        candidate = getattr(raw, "structuredContent", raw)
    if not isinstance(candidate, dict):
        raise IRValidationError("MCP call_tool returned a non-object payload")
    return dict(candidate)


def _validate(
    payload: dict[str, Any], schema: dict[str, object], *, kind: str, tool_id: str
) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise IRValidationError(
            f"MCP tool {tool_id!r} {kind} schema validation failed: {first.message}",
            tool_id=tool_id,
            violation=f"mcp-{kind}-schema",
        )


async def bind(server: object, *, capabilities: Capabilities) -> list[ToolSpec]:
    if isinstance(server, _MCPSessionLike):
        _SESSION_CAPS[id(server)] = capabilities
        await server.initialize()
        result = await server.list_tools()
        return [_translate(t) for t in _extract_tools(result)]
    return await _bind_via_stdio(server, capabilities=capabilities)


async def _bind_via_stdio(server: object, *, capabilities: Capabilities) -> list[ToolSpec]:
    from mcp import ClientSession  # type: ignore[import-not-found]
    from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

    async with (
        stdio_client(server) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        _SESSION_CAPS[id(session)] = capabilities
        await session.initialize()
        result = await session.list_tools()
        return [_translate(t) for t in _extract_tools(result)]


async def call_tool(
    session: _MCPSessionLike,
    tool: ToolSpec,
    arguments: dict[str, Any],
    *,
    capabilities: Capabilities | None = None,
) -> dict[str, Any]:
    effective_caps = capabilities if capabilities is not None else _SESSION_CAPS.get(id(session))
    if effective_caps is not None and not effective_caps.check(tool):
        raise CapabilityError(
            f"tool {tool.name!r} requires permissions not granted",
            capability=",".join(tool.permissions),
            tool_id=tool.name,
            deployment="mcp",
        )
    _validate(arguments, tool.input_schema, kind="input", tool_id=tool.name)
    raw = await session.call_tool(tool.name, dict(arguments))
    payload = _extract_structured(raw)
    _validate(payload, tool.output_schema, kind="output", tool_id=tool.name)
    sanitized = _sanitize(payload)
    if not isinstance(sanitized, dict):
        raise IRValidationError(
            f"MCP tool {tool.name!r} returned non-object output", tool_id=tool.name
        )
    return sanitized
'''


_MCP_ADAPTER_TEST = """\
import asyncio
from dataclasses import dataclass, field
from typing import Any

from adapter import bind, call_tool

from stargraph.errors import CapabilityError
from stargraph.ir import ToolSpec
from stargraph.security import Capabilities, CapabilityClaim


@dataclass(frozen=True)
class StubTool:
    name: str
    description: str
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]


@dataclass
class _ListResult:
    tools: list = field(default_factory=list)


@dataclass
class _CallResult:
    structuredContent: dict[str, Any]


class StubSession:
    def __init__(self, tools, script):
        self._tools = list(tools)
        self._script = dict(script)
        self.initialized = False
        self.calls = []

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return _ListResult(tools=list(self._tools))

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return _CallResult(structuredContent=dict(self._script[name]))


def _echo():
    return StubTool(
        name="echo",
        description="echo",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
            "required": ["echoed"],
            "additionalProperties": False,
        },
    )


def _read_secret():
    return StubTool(
        name="read-secret",
        description="secret",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {"contents": {"type": "string"}},
            "required": ["contents"],
            "additionalProperties": False,
        },
    )


_OPEN = Capabilities(
    default_deny=False, granted={CapabilityClaim(name="fs.read", scope="/secrets/*")}
)


def test_bind_translates():
    sess = StubSession([_echo(), _read_secret()], {"echo": {"echoed": "hi"}})
    specs = asyncio.run(bind(sess, capabilities=_OPEN))
    assert len(specs) == 2
    assert all(isinstance(s, ToolSpec) for s in specs)
    assert {s.name for s in specs} == {"echo", "read-secret"}
    assert sess.initialized is True


def test_input_validation():
    sess = StubSession([_echo()], {"echo": {"echoed": "ok"}})
    echo = asyncio.run(bind(sess, capabilities=_OPEN))[0]
    try:
        asyncio.run(call_tool(sess, echo, {"text": 123}))
        raise AssertionError("expected an input validation error")
    except Exception as e:
        assert "input" in str(e).lower() or "schema" in str(e).lower()


def test_output_validation():
    sess = StubSession([_echo()], {"echo": {"wrong": "x"}})
    echo = asyncio.run(bind(sess, capabilities=_OPEN))[0]
    try:
        asyncio.run(call_tool(sess, echo, {"text": "hi"}))
        raise AssertionError("expected an output validation error")
    except Exception as e:
        assert "output" in str(e).lower() or "schema" in str(e).lower()


def test_sanitize():
    sess = StubSession([_echo()], {"echo": {"echoed": "<b>x</b>" + chr(7) + chr(27) + "[31m"}})
    echo = asyncio.run(bind(sess, capabilities=_OPEN))[0]
    out = asyncio.run(call_tool(sess, echo, {"text": "hi"}))
    assert "<b>" not in out["echoed"]
    assert "&lt;" in out["echoed"]
    assert chr(7) not in out["echoed"]
    assert chr(27) not in out["echoed"]


def test_capability_gate():
    sess = StubSession([_read_secret()], {"read-secret": {"contents": "TOP"}})
    spec = asyncio.run(bind(sess, capabilities=Capabilities()))[0]
    gate_sess = StubSession([_read_secret()], {"read-secret": {"contents": "TOP"}})
    asyncio.run(bind(gate_sess, capabilities=Capabilities()))
    try:
        asyncio.run(call_tool(gate_sess, spec, {"path": "/secrets/k"}))
        raise AssertionError("expected CapabilityError")
    except CapabilityError:
        pass
    assert gate_sess.calls == []
"""


SEEDS: list[dict[str, Any]] = [
    _pair(
        "70030000001",
        "an MCP adapter: bind a tool catalogue into Stargraph ToolSpecs and gate "
        "every call_tool through capability check, input/output schema validation, "
        "and output sanitization",
        "mcp",
        "mcp",
        {},
        _MCP_ADAPTER_SOURCE,
        _MCP_ADAPTER_TEST,
    ),
]
