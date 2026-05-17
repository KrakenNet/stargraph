# SPDX-License-Identifier: Apache-2.0
"""Shared types for the Harbor plugin contract.

Centralises the type aliases referenced from
:mod:`harbor.plugin.hookspecs` so plugin authors get a real contract
instead of bare ``Any``. Imports are fenced behind ``TYPE_CHECKING``
where the source module pulls heavyweight runtime deps (FastAPI,
Capabilities, FathomAdapter) — keeps ``harbor.plugin`` import-light
for hosts that wire plugins without running the full engine.

Phase-2 backfill (TODO from ``hookspecs.py`` docstring): ``PluginManager``,
``ToolCall``, ``ToolResult``, ``StoreSpec``, ``PackSpec`` were ``Any``;
this module gives each a concrete type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    import pluggy

    from harbor.ir._models import StoreSpec as _IRStoreSpec
    from harbor.runtime.tool_exec import ToolResult as _RuntimeToolResult


__all__ = [
    "BosunAction",
    "MCPAdapterSpec",
    "PackSpec",
    "PluginManager",
    "Route",
    "StoreSpec",
    "ToolCall",
    "ToolResult",
]


class BosunAction(BaseModel):
    """Pluggy ``authorize_action`` payload model.

    Typed parameter for :func:`harbor.plugin.hookspecs.authorize_action`.
    Three minimal-viable fields per ``shared.md §4``.
    """

    action_kind: str
    target: str
    payload: dict[str, Any]


# Pluggy plugin manager handle — used by lifecycle hooks
# (``harbor_startup`` / ``harbor_shutdown``). Aliased so plugin authors
# can ``from harbor.plugin.types import PluginManager`` without a direct
# pluggy dependency at import time.
type PluginManager = "pluggy.PluginManager"


@dataclass(slots=True, frozen=True)
class ToolCall:
    """Plugin-observable tool invocation request (passed to ``before_tool_call`` /
    ``after_tool_call`` hookspecs).

    Distinct from :class:`harbor.runtime.events.ToolCallEvent` (which is a
    bus event with timestamp / step / run_id) — this is the lighter-weight
    record plugin observers need: tool identity + arguments + a stable
    ``call_id`` for correlation with the matching ``after_tool_call``
    invocation.

    Frozen so plugins can stash references without worrying about mutation.
    """

    tool_name: str
    """Tool name within its namespace (e.g. ``broker_request``)."""

    namespace: str
    """Tool namespace (e.g. ``nautilus``)."""

    args: dict[str, Any] = field(default_factory=dict[str, Any])
    """Validated arguments passed to the tool."""

    call_id: str = ""
    """Stable correlation id linking ``before_tool_call`` to ``after_tool_call``."""


# ``ToolResult`` is the existing dataclass at
# :class:`harbor.runtime.tool_exec.ToolResult` (output, replayed, tokens).
# Aliased here so the public plugin contract has a stable import path
# even if the implementation moves.
type ToolResult = "_RuntimeToolResult"


# ``StoreSpec`` is the canonical IR record for store registrations
# (design §3.16). Re-exported so ``register_stores()`` hook
# implementations have a typed return contract.
type StoreSpec = "_IRStoreSpec"


@dataclass(slots=True, frozen=True)
class PackSpec:
    """Plugin-contributed Bosun rule pack registration.

    Distinct from :class:`harbor.ir._models.PackMount` (which is the
    IR-level reference to a pack — id + version + requires block). A
    ``PackSpec`` is what a plugin's ``register_packs()`` hook returns:
    enough metadata for the loader to locate, signature-verify, and
    register the pack with the Bosun runtime.

    Phase-2 contract: minimal triple of ``id`` / ``version`` /
    ``manifest_path``. Capabilities the pack requires are read from the
    pack manifest at load time; not duplicated here.
    """

    id: str
    """Stable pack id (e.g. ``cve_rem.routing``)."""

    version: str
    """SemVer pack version."""

    manifest_path: str
    """Filesystem path to the pack's ``manifest.yaml``. Loader resolves
    ``rules.clp`` / signature / etc. relative to this directory."""


# FastAPI ``BaseRoute`` returned by trigger plugins. Kept as
# ``Any`` (string-quoted to defer import) so hosts without FastAPI
# installed can still import the plugin contract. Tightening to a real
# starlette type lands when the serve module's FastAPI dep is
# unconditional.
type Route = Any


@dataclass(slots=True, frozen=True)
class MCPAdapterSpec:
    """Plugin-contributed MCP server registration (FR-25, design §3.3.2).

    A plugin's ``register_mcp_adapters()`` hook returns a list of these.
    The serve / engine wiring picks them up and feeds each ``server``
    into :func:`harbor.adapters.mcp.bind` at the appropriate lifespan
    point, gating with the supplied capability set.

    The ``server`` field is intentionally typed loosely (``Any``) — it
    matches the same ``object`` argument that
    :func:`harbor.adapters.mcp.bind` accepts: either an
    ``mcp.StdioServerParameters`` (real stdio MCP server) or a
    session-shaped duck-typed object (in-memory tests / custom
    transports). The adapter dispatches at runtime on
    :class:`harbor.adapters.mcp._MCPSessionLike`.
    """

    name: str
    """Stable adapter name (used to namespace the registered tools)."""

    server: Any
    """``StdioServerParameters`` or session-shaped object (see docstring)."""

    required_capabilities: list[str] = field(default_factory=list[str])
    """Capabilities the registered tools collectively require. Used by
    the loader to capability-gate the bind call before the server is
    contacted; per-call gating still fires inside
    :func:`harbor.adapters.mcp.call_tool`."""
