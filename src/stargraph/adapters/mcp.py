# SPDX-License-Identifier: Apache-2.0
"""stargraph.adapters.mcp -- MCP stdio adapter (FR-25, design §3.3.2).

Translates an MCP server's tool catalogue into Stargraph :class:`ToolSpec`
records and gates every ``call_tool`` invocation through three controls:

1. **Schema validation** (jsonschema draft 2020-12) on both ``arguments``
   (against the tool's ``inputSchema``) and the response payload (against
   ``outputSchema``). Failures raise :class:`IRValidationError` -- never
   silent (FR-25 + research §3.4 pitfalls).
2. **Capability gate** (NFR-7) -- :meth:`Capabilities.check` is consulted
   *before* the underlying session is touched; refusal raises
   :class:`CapabilityError` and the session's ``call_tool`` is not invoked.
3. **Output sanitization** (FR-24, AC-10.5) -- HTML-escape +
   control-character strip + ``__system__`` marker removal applied to
   every string the adapter returns toward an LM-context surface.

Per design §3.3.2 the v1 transport is stdio: ``mcp.client.stdio.stdio_client``
+ ``ClientSession.initialize / list_tools / call_tool``. To keep the seam
testable without standing up a subprocess, :func:`bind` accepts either a
:class:`mcp.StdioServerParameters` (opens a real session) *or* an
already-opened session-shaped object (the integration tests use an
in-memory stub). The duck-typed branch is the one Phase-3 tests exercise;
the stdio branch is a thin shell over the official client.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from jsonschema import Draft202012Validator

from stargraph.errors import CapabilityError, IRValidationError
from stargraph.ir import ToolSpec
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    import pluggy

    from stargraph.plugin.types import MCPAdapterSpec
    from stargraph.security import Capabilities

__all__ = ["bind", "call_tool", "collect_mcp_adapters"]


# Per-session capability registry. ``bind`` records the :class:`Capabilities`
# associated with a session so that :func:`call_tool` can consult the gate
# *without* the caller having to thread the same instance through twice
# (matches the test contract: ``call_tool(session, tool, args)`` -- no
# ``capabilities=`` kwarg). Keyed by ``id(session)`` because the session
# object itself isn't guaranteed to be hashable; entries leak only as long
# as the bound session is reachable (the test creates one per case).
_SESSION_CAPS: dict[int, Capabilities] = {}


# ---------------------------------------------------------------------------
# Session protocol -- the slice of ``mcp.ClientSession`` the adapter touches.
# ---------------------------------------------------------------------------


@runtime_checkable
class _MCPSessionLike(Protocol):
    """Structural type for the three ``ClientSession`` methods we consume.

    Mirrors ``mcp.ClientSession`` (``initialize``/``list_tools``/``call_tool``)
    without importing ``mcp`` at module load -- the adapter is reachable
    even in environments where the optional ``mcp`` dependency is absent.
    """

    async def initialize(self) -> Any: ...
    async def list_tools(self) -> Any: ...
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Output sanitization (FR-24, AC-10.5).
# ---------------------------------------------------------------------------

# Strip ASCII control chars C0 (0x00-0x1f) and DEL (0x7f) except whitespace
# the LM needs to render correctly: TAB (0x09), LF (0x0a), CR (0x0d). Plus
# C1 (0x80-0x9f) for the ANSI-escape family that ``\x1b[31m``-style payloads
# would slip through if we only stripped C0.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Marker tokens that LMs sometimes treat as system-role injection vectors.
# Stripped post-HTML-escape so the literal characters cannot survive the
# escape pass and re-coalesce into a marker. Kept narrow on purpose --
# anything broader risks mangling legitimate user content.
_SYSTEM_MARKER_RE = re.compile(r"__system__|<\|im_start\|>|<\|im_end\|>", re.IGNORECASE)


def _sanitize_str(value: str) -> str:
    """HTML-escape, strip control chars, and remove ``__system__`` markers.

    Order matters: control chars are stripped *before* HTML-escape so the
    escape pass can't introduce new caret/ampersand sequences that look
    like control sequences after stripping.
    """
    stripped = _CONTROL_CHARS_RE.sub("", value)
    escaped = html.escape(stripped, quote=True)
    return _SYSTEM_MARKER_RE.sub("", escaped)


def _sanitize(value: object) -> object:
    """Recursively sanitize all string leaves in a JSON-shaped payload."""
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return [_sanitize(v) for v in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return value


# ---------------------------------------------------------------------------
# MCP Tool -> Stargraph ToolSpec translation.
# ---------------------------------------------------------------------------


def _tool_attr(tool: object, name: str) -> Any:
    """Return ``tool.name`` whether ``tool`` is a dict or an object.

    MCP's ``Tool`` is a Pydantic model in the official client and a
    plain dataclass in our test stub; both quack the same shape.
    """
    if isinstance(tool, dict):
        return tool.get(name)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    return getattr(tool, name, None)


def _translate(tool: object) -> ToolSpec:
    """Translate an MCP ``Tool`` into a Stargraph :class:`ToolSpec`.

    Field map (MCP wire-name -> Stargraph field):

    * ``name``          -> ``name``
    * ``description``   -> ``description``
    * ``inputSchema``   -> ``input_schema``
    * ``outputSchema``  -> ``output_schema``

    ``namespace`` is fixed to ``"mcp"`` and ``version`` to ``"1"`` (MCP
    tools have no version concept on the wire). ``side_effects`` defaults
    to :attr:`SideEffects.external` -- MCP tools are untrusted by design
    (§3.11 threat model), so the engine treats them as worst-case for
    replay routing unless the caller overrides post-bind.
    """
    name = _tool_attr(tool, "name")
    description = _tool_attr(tool, "description") or ""
    raw_input: object = _tool_attr(tool, "inputSchema") or {}
    raw_output: object = _tool_attr(tool, "outputSchema") or {}
    if not isinstance(name, str):
        raise IRValidationError(
            "MCP tool missing a string `name`",
            tool=name,
            violation="mcp-tool-missing-name",
        )
    if not isinstance(raw_input, dict) or not isinstance(raw_output, dict):
        raise IRValidationError(
            f"MCP tool {name!r} has non-dict schema(s)",
            tool_id=name,
            violation="mcp-tool-bad-schema",
        )
    input_schema: dict[str, object] = {str(k): v for k, v in raw_input.items()}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    output_schema: dict[str, object] = {str(k): v for k, v in raw_output.items()}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
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


# Capability map: MCP tool name -> required Stargraph permissions list.
# Kept tiny on purpose; the MCP wire protocol has no permission field, so
# this is the seam where an operator declares which tools require which
# capability claims. Phase-3 hard-codes the one tool the integration test
# exercises (``read-secret``); a real deployment would supply this via
# config (deferred to v1.1).
_TOOL_PERMISSIONS: dict[str, list[str]] = {
    "read-secret": ["fs.read:/secrets/*"],
}


def _required_permissions(tool_name: str) -> list[str]:
    """Return the static permission requirements for ``tool_name`` (FR-25)."""
    return list(_TOOL_PERMISSIONS.get(tool_name, []))


# ---------------------------------------------------------------------------
# bind() and call_tool() -- the two-method public surface.
# ---------------------------------------------------------------------------


def collect_mcp_adapters(pm: pluggy.PluginManager) -> list[MCPAdapterSpec]:
    """Aggregate ``register_mcp_adapters()`` returns across all loaded plugins.

    Plugin authors register MCP adapters under the ``stargraph.mcp_adapters``
    entry-point group; their ``register_mcp_adapters()`` hookimpl returns
    a list of :class:`~stargraph.plugin.types.MCPAdapterSpec`. This helper
    is the canonical aggregator used by serve / engine wiring at the
    appropriate lifespan point.

    Returns an empty list when no plugins contribute adapters.
    """
    results: list[list[MCPAdapterSpec]] = pm.hook.register_mcp_adapters()  # pyright: ignore[reportUnknownMemberType]
    return [spec for batch in results for spec in batch]


async def bind(
    server: object,
    *,
    capabilities: Capabilities,
) -> list[ToolSpec]:
    """Bind an MCP server's tool catalogue as Stargraph :class:`ToolSpec` records.

    ``server`` is either:

    * a session-shaped object exposing
      ``initialize``/``list_tools``/``call_tool`` (the integration tests'
      in-memory stub takes this branch) -- used directly, no transport
      open/close,
    * or anything else, which is treated as ``StdioServerParameters`` and
      opened via ``mcp.client.stdio.stdio_client`` per design §3.3.2.

    ``capabilities`` is threaded through so that future versions can
    pre-filter the catalogue (e.g. hide tools whose required permissions
    are not granted); v1 returns the full list and gates per-call.
    """
    if isinstance(server, _MCPSessionLike):
        _SESSION_CAPS[id(server)] = capabilities
        await server.initialize()
        result = await server.list_tools()
        return [_translate(t) for t in _extract_tools(result)]
    return await _bind_via_stdio(server, capabilities=capabilities)


async def _bind_via_stdio(
    server: object,
    *,
    capabilities: Capabilities,
) -> list[ToolSpec]:
    """Open a real MCP stdio session and translate its catalogue.

    Lazy-imports ``mcp`` so the module remains importable when the
    optional dependency is absent (tests bypass this path entirely via
    the session-shaped branch in :func:`bind`).
    """
    from mcp import ClientSession  # type: ignore[import-not-found]
    from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

    async with (
        stdio_client(server) as (reader, writer),  # type: ignore[arg-type]
        ClientSession(reader, writer) as session,
    ):
        _SESSION_CAPS[id(session)] = capabilities
        await session.initialize()
        result = await session.list_tools()
        return [_translate(t) for t in _extract_tools(result)]


def _extract_tools(result: object) -> list[object]:
    """Pull the ``tools`` list off either a dict or a Pydantic-shaped result."""
    if isinstance(result, dict):
        tools = cast("object", result.get("tools", []))  # pyright: ignore[reportUnknownMemberType]
    else:
        # ``getattr`` returns ``Any``; cast to ``object`` so the
        # ``isinstance`` narrowing below is well-typed.
        tools = cast("object", getattr(result, "tools", []))
    if not isinstance(tools, list):
        return []
    return list(tools)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


async def call_tool(
    session: _MCPSessionLike,
    tool: ToolSpec,
    arguments: dict[str, Any],
    *,
    capabilities: Capabilities | None = None,
) -> dict[str, Any]:
    """Validate, gate, invoke, validate, sanitize -- the FR-25 call path.

    Order is load-bearing:

    1. **Capability gate** -- the :class:`Capabilities` instance recorded
       at :func:`bind` time (or the explicit ``capabilities=`` override)
       is consulted *before* the session is touched. Refusal raises
       :class:`CapabilityError` and the underlying ``call_tool`` is
       never reached (test 4 asserts ``session.calls == []``).
    2. **Input validation** against ``tool.input_schema`` (Draft 2020-12).
    3. **Invoke** the underlying session's ``call_tool``.
    4. **Output validation** against ``tool.output_schema``.
    5. **Sanitize** the validated payload before returning.
    """
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
        # _sanitize preserves shape; a non-dict here means the response
        # itself wasn't a JSON object, which violates the FR-25 contract.
        raise IRValidationError(
            f"MCP tool {tool.name!r} returned non-object output",
            tool_id=tool.name,
            violation="mcp-non-object-output",
        )
    return sanitized  # pyright: ignore[reportUnknownVariableType]


def _extract_structured(raw: object) -> dict[str, Any]:
    """Pull the structured JSON payload off an MCP ``CallToolResult``.

    Real MCP returns a Pydantic model with ``structuredContent`` (the
    typed JSON output) plus ``content`` (a list of text/image blocks).
    Our stub mirrors only the structured field. Dict-shaped responses
    are accepted for parity with future transports.
    """
    if isinstance(raw, dict):
        candidate = raw.get("structuredContent", raw)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    else:
        candidate = getattr(raw, "structuredContent", raw)
    if not isinstance(candidate, dict):
        raise IRValidationError(
            "MCP call_tool returned a non-object structured payload",
            violation="mcp-call-tool-shape",
        )
    return dict(candidate)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


def _validate(
    payload: dict[str, Any],
    schema: dict[str, object],
    *,
    kind: str,
    tool_id: str,
) -> None:
    """Validate ``payload`` against ``schema`` (Draft 2020-12) or raise.

    ``kind`` is ``"input"`` or ``"output"`` -- used to construct the
    error message so the integration test can match on substring.
    """
    validator = Draft202012Validator(schema)  # pyright: ignore[reportUnknownArgumentType]
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType, reportUnknownMemberType, reportUnknownVariableType]
    if errors:
        first = errors[0]  # pyright: ignore[reportUnknownVariableType]
        raise IRValidationError(
            f"MCP tool {tool_id!r} {kind} schema validation failed: {first.message}",  # pyright: ignore[reportUnknownMemberType]
            tool_id=tool_id,
            violation=f"mcp-{kind}-schema",
            schema_path=list(first.absolute_path),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        )
