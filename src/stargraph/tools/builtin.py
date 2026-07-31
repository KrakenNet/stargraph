# SPDX-License-Identifier: Apache-2.0
"""In-process seeding of Stargraph's own built-in tools.

Stargraph's in-tree tools are NOT loaded through the plugin entry-point
pipeline -- that pipeline exists for *external* distributions (each of
which must ship a ``stargraph_plugin`` manifest factory). Routing our own
dist through it would force a self-manifest and pay entry-point metadata
costs for tools we can import directly; the loader therefore skips the
core ``stargraph`` distribution, and every consumer that builds a
:class:`~stargraph.registry.tools.ToolRegistry` seeds the built-ins with
:func:`seed_builtin_tools` instead.

Tools that live behind an optional extra (currently ``rl:train_ppo``
behind ``stargraph[rl]``) are seeded only when their import succeeds --
the extra defines availability, and skipping it is logged at info.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

from stargraph.logging import get_logger

if TYPE_CHECKING:
    from stargraph.registry.tools import Tool, ToolRegistry

_logger = get_logger("stargraph.tools.builtin")

#: ``module:attr`` refs of always-available built-in tools (hard-dep
#: imports only). Kept as refs -- not direct imports -- so importing this
#: module stays cheap until a registry is actually seeded.
_BUILTIN_TOOL_REFS: tuple[str, ...] = (
    "stargraph.tools.nautilus.broker_request:broker_request",
    "stargraph.tools.servicenow.create_change_request:create_change_request",
    "stargraph.tools.servicenow.cmdb_query_software:cmdb_query_software",
    "stargraph.tools.servicenow.cmdb_traverse_runs_on:cmdb_traverse_runs_on",
    "stargraph.tools.servicenow.cmdb_resolve_hosts:cmdb_resolve_hosts",
    "stargraph.tools.servicenow.patch_cr_state:patch_cr_state",
    "stargraph.tools.servicenow.patch_work_notes:patch_work_notes",
    "stargraph.tools.servicenow.poll_approval:poll_approval",
    "stargraph.tools.servicenow.upload_attachment:upload_attachment",
    "stargraph.tools.servicenow.table_crud:table_query",
    "stargraph.tools.servicenow.table_crud:table_create",
)

#: ``module:attr`` -> extra name, for tools gated behind optional extras.
_EXTRA_TOOL_REFS: dict[str, str] = {
    "stargraph.rl.trainer:train_ppo": "rl",
}


def _load_tool(ref: str) -> Tool:
    module_path, _, attr = ref.partition(":")
    module = importlib.import_module(module_path)
    return cast("Tool", getattr(module, attr))


def seed_builtin_tools(registry: ToolRegistry) -> ToolRegistry:
    """Register every in-tree built-in tool on ``registry`` and return it.

    Raises :class:`~stargraph.errors.PluginLoadError` on id conflict --
    call once per fresh registry, before any plugin contributions land.
    Extras-gated tools that fail to import are skipped with an info log
    (install the named extra to include them).
    """
    for ref in _BUILTIN_TOOL_REFS:
        registry.register(_load_tool(ref))
    for ref, extra in _EXTRA_TOOL_REFS.items():
        try:
            tool = _load_tool(ref)
        except ImportError:
            _logger.info(
                "builtin_tool_skipped_missing_extra",
                ref=ref,
                extra=extra,
                hint=f"pip install stargraph[{extra}]",
            )
            continue
        registry.register(tool)
    return registry


__all__ = ["seed_builtin_tools"]
