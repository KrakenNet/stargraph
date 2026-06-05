# SPDX-License-Identifier: Apache-2.0
"""Hook specifications for the Harbor plugin system.

Declarations only: every function body is ``pass``. Pluggy uses these
signatures as the contract that plugin implementations must match.

``authorize_action`` uses ``firstresult=True`` so the first non-``None``
result wins, supporting Bosun's first-deny authorisation semantics. The
``register_*`` collect-all hooks intentionally omit ``firstresult`` so
every plugin's contributions are aggregated.

Type aliases used by the hookspecs (``PluginManager``, ``ToolCall``,
``ToolResult``, ``StoreSpec``, ``PackSpec``, ``Route``) live in
:mod:`harbor.plugin.types`. Phase-2 backfill (resolved): plugin authors
import them from that module and get a real contract instead of bare
:data:`Any`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harbor.plugin._markers import hookspec

if TYPE_CHECKING:
    from harbor.ir._models import SkillSpec, ToolSpec
    from harbor.plugin.types import (
        BosunAction,
        MCPAdapterSpec,
        PackSpec,
        PluginManager,
        Route,
        StoreSpec,
        ToolCall,
        ToolResult,
    )


@hookspec
def harbor_startup(pm: PluginManager) -> None:
    """Lifecycle: invoked once after the plugin manager finishes loading."""


@hookspec
def harbor_shutdown(pm: PluginManager) -> None:
    """Lifecycle: invoked once during graceful shutdown."""


@hookspec
def register_tools() -> list[ToolSpec]:
    """Collect-all: each plugin returns the tools it provides."""
    return []


@hookspec
def before_tool_call(call: ToolCall) -> None:
    """Observation hook fired immediately before a tool invocation."""


@hookspec
def after_tool_call(call: ToolCall, result: ToolResult) -> None:
    """Observation hook fired immediately after a tool invocation."""


@hookspec
def register_skills() -> list[SkillSpec]:
    """Collect-all: each plugin returns the skills it provides."""
    return []


@hookspec
def register_stores() -> list[StoreSpec]:
    """Collect-all: each plugin returns the stores it provides."""
    return []


@hookspec
def register_packs() -> list[PackSpec]:
    """Collect-all: each plugin returns the packs it provides."""
    return []


@hookspec
def register_mcp_adapters() -> list[MCPAdapterSpec]:
    """Collect-all: each plugin returns the MCP adapters it provides (FR-25).

    The serve / engine wiring drives :func:`harbor.adapters.mcp.bind`
    against each spec at the appropriate lifespan point. v1 transport is
    stdio; the adapter dispatches at runtime on session-shape duck-typing
    so plugins can ship in-memory adapters for tests too.
    """
    return []


@hookspec(firstresult=True)
def authorize_action(action: BosunAction) -> bool | None:
    """Authorisation hook: first non-``None`` result wins.

    Returning ``False`` denies, ``True`` allows, ``None`` abstains so
    the next plugin gets a turn. Implements Bosun first-deny semantics.
    """


@hookspec
def trigger_init(deps: dict[str, Any]) -> None:
    """Trigger lifecycle: invoked once at lifespan startup.

    The plugin sets up internal state from the ``deps`` mapping (which
    carries the serve ``ServeContext`` plus any wiring the plugin needs).
    Pluggy's default first-exception-halt is intentionally overridden by
    :func:`harbor.plugin.triggers_dispatcher.dispatch_trigger_lifecycle`:
    one trigger plugin's failure must not block other triggers from
    initialising.
    """


@hookspec
def trigger_start(deps: dict[str, Any]) -> None:
    """Trigger lifecycle: invoked when the scheduler starts.

    Plugin begins emitting ``TriggerEvent``s. Same per-plugin try/except
    isolation as :func:`trigger_init`.
    """


@hookspec
def trigger_stop(deps: dict[str, Any]) -> None:
    """Trigger lifecycle: invoked on graceful shutdown.

    Plugin drains in-flight work and stops emitting events. Same
    per-plugin try/except isolation as :func:`trigger_init`.
    """


@hookspec
def trigger_routes() -> list[Route]:
    """Collect-all: each trigger plugin returns FastAPI routes to mount.

    Webhook triggers return their HTTP endpoints here; cron-only triggers
    return ``[]``. The serve app gathers and mounts every plugin's routes
    during lifespan setup.
    """
    return []


class TriggerHookSpec:
    """Namespace alias for the trigger lifecycle hookspecs (design §6.3).

    Pluggy registers hookspecs from the *module* (see
    :func:`harbor.plugin.loader.build_plugin_manager`), so this class is
    documentation/grouping only — it does not carry pluggy decorators.
    Callers that prefer a class-shaped reference (per design §6.3) can
    import :class:`TriggerHookSpec` and access the bound hookspec
    callables.

    .. note::
       Per-plugin try/except isolation lives in
       :mod:`harbor.plugin.triggers_dispatcher`. Direct ``pm.hook.<name>()``
       calls fall back to pluggy's first-exception-halt default and are
       unsafe for trigger lifecycles.
    """

    trigger_init = staticmethod(trigger_init)
    trigger_start = staticmethod(trigger_start)
    trigger_stop = staticmethod(trigger_stop)
    trigger_routes = staticmethod(trigger_routes)


# Expose ``firstresult`` as a direct attribute on each hookspec function
# (pluggy stashes its config inside ``<project>_spec`` dicts, but Harbor
# exposes a stable boolean attribute so callers and tests can inspect a
# hookspec's collect semantics without reaching into pluggy internals).
authorize_action.firstresult = True  # type: ignore[attr-defined]
for _hook in (
    harbor_startup,
    harbor_shutdown,
    register_tools,
    before_tool_call,
    after_tool_call,
    register_skills,
    register_stores,
    register_packs,
    register_mcp_adapters,
    trigger_init,
    trigger_start,
    trigger_stop,
    trigger_routes,
):
    _hook.firstresult = False  # type: ignore[attr-defined]
del _hook
