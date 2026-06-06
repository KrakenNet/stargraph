# SPDX-License-Identifier: Apache-2.0
"""Per-plugin try/except dispatcher for trigger lifecycle hooks.

Pluggy's default behaviour for non-``firstresult`` hooks is to call every
implementation in registration order; if any impl raises, pluggy halts
the iteration and propagates the exception. That behaviour is unsafe for
trigger lifecycle hooks (``trigger_init`` / ``trigger_start`` /
``trigger_stop``): a single misbehaving trigger plugin would prevent the
remaining trigger plugins from initialising, starting, or shutting down
cleanly (design §6.3, FR-2, AC-12.2).

This module provides :func:`dispatch_trigger_lifecycle` which iterates
the registered hook implementations directly and isolates exceptions per
implementation. Each plugin is given a turn; failures are logged via the
shared structlog ``stargraph.plugin.triggers`` logger and reported in the
returned ``DispatchResult`` list for observability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from stargraph.logging import get_logger

if TYPE_CHECKING:
    import pluggy

_logger = get_logger("stargraph.plugin.triggers")

LifecycleHook = Literal["trigger_init", "trigger_start", "trigger_stop"]


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of one plugin's hook invocation.

    Attributes:
        plugin_name: Pluggy plugin name (``pm.get_name(plugin)``).
        success: ``True`` iff the hook returned without raising.
        result: Hook return value when ``success``; otherwise ``None``.
        error: The caught exception when ``success`` is ``False``.
    """

    plugin_name: str
    success: bool
    result: Any = None
    error: BaseException | None = None


def dispatch_trigger_lifecycle(
    pm: pluggy.PluginManager,
    hook_name: LifecycleHook,
    deps: dict[str, Any],
) -> list[DispatchResult]:
    """Invoke a trigger lifecycle hook on every registered plugin.

    Iterates ``pm.list_plugin_distinfo()``-like surface (uses
    ``pm.get_plugins()`` for plugin objects + ``pm.get_name`` for stable
    names) and, for each plugin that exposes ``hook_name``, calls the
    bound method inside a per-plugin ``try/except``. Exceptions are
    logged with the plugin name and captured in the returned list so the
    caller can surface them to operators (e.g. ``/health`` or audit
    sink) without halting the other triggers.

    Args:
        pm: The Stargraph pluggy plugin manager.
        hook_name: Which lifecycle hook to invoke.
        deps: The dependency mapping passed to each plugin's hook
            implementation (carries the serve ``ServeContext`` etc.).

    Returns:
        One :class:`DispatchResult` per plugin attempted, in iteration
        order. Plugins that do not implement ``hook_name`` are skipped
        (no entry in the result list).
    """
    results: list[DispatchResult] = []
    for plugin in pm.get_plugins():
        impl = getattr(plugin, hook_name, None)
        if impl is None or not callable(impl):
            continue
        plugin_name = pm.get_name(plugin) or repr(plugin)
        try:
            value = impl(deps)
        except Exception as exc:  # plugin isolation — never propagate
            _logger.error(
                "trigger_lifecycle_failed",
                plugin=plugin_name,
                hook=hook_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            results.append(
                DispatchResult(
                    plugin_name=plugin_name,
                    success=False,
                    result=None,
                    error=exc,
                )
            )
            continue
        results.append(
            DispatchResult(
                plugin_name=plugin_name,
                success=True,
                result=value,
                error=None,
            )
        )
    return results


def collect_trigger_routes(pm: pluggy.PluginManager) -> list[DispatchResult]:
    """Invoke ``trigger_routes`` on every registered plugin.

    Same per-plugin try/except isolation as
    :func:`dispatch_trigger_lifecycle`, but the hook takes no arguments
    and returns a list of routes. The caller flattens the per-plugin
    ``result`` lists into a single mount-list.
    """
    results: list[DispatchResult] = []
    for plugin in pm.get_plugins():
        impl = getattr(plugin, "trigger_routes", None)
        if impl is None or not callable(impl):
            continue
        plugin_name = pm.get_name(plugin) or repr(plugin)
        try:
            value = impl()
        except Exception as exc:  # plugin isolation — never propagate
            _logger.error(
                "trigger_routes_failed",
                plugin=plugin_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            results.append(
                DispatchResult(
                    plugin_name=plugin_name,
                    success=False,
                    result=None,
                    error=exc,
                )
            )
            continue
        results.append(
            DispatchResult(
                plugin_name=plugin_name,
                success=True,
                result=value,
                error=None,
            )
        )
    return results
