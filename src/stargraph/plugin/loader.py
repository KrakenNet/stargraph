# SPDX-License-Identifier: Apache-2.0
"""Two-stage Stargraph plugin loader.

Stage 1 -- discovery (no import of plugin code beyond manifest factories):
    For each of the four plugin groups
    (``stargraph.tools``, ``stargraph.skills``, ``stargraph.stores``,
    ``stargraph.packs``), enumerate entry points via
    :func:`importlib.metadata.entry_points`. Group entries by their
    distribution. For each unique distribution that contributes any
    entries, load and validate its ``stargraph_plugin`` manifest factory
    via :func:`stargraph.plugin._manifest._load_and_validate_manifest`.
    The manifest carries ``api_version``, ``namespaces`` and ``order``;
    namespace conflicts and major version mismatches abort the load.

Stage 2 -- registration:
    Sort the surviving distributions by ``manifest.order`` ascending
    (collisions = :class:`PluginLoadError`). For each distribution's
    entry points (across all four groups) call ``ep.load()`` and then
    ``pm.register(...)`` -- this is the only point at which plugin
    code actually imports. Finally,
    :meth:`pluggy.PluginManager.load_setuptools_entrypoints` picks up
    any standalone ``stargraph`` entry-point group plugins (pure pluggy
    hookimpls without ToolSpec / SkillSpec / StoreSpec / PackSpec
    contributions) -- this mirrors Datasette's
    ``DEFAULT_PLUGINS + entry_points('datasette')`` flow.

Set the ``STARGRAPH_TRACE_PLUGINS`` environment variable (any non-empty
value) to log every discovery, validation and registration step at
INFO level for debugging.
"""

from __future__ import annotations

import os
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any, cast

import pluggy

from stargraph.errors import PluginLoadError
from stargraph.logging import get_logger

from . import hookspecs
from ._manifest import _detect_namespace_conflict, _load_and_validate_manifest
from ._markers import PROJECT

if TYPE_CHECKING:
    from stargraph.ir import PluginManifest

GROUPS: tuple[str, ...] = (
    "stargraph.tools",
    "stargraph.skills",
    "stargraph.stores",
    "stargraph.packs",
    "stargraph.triggers",
    "stargraph.mcp_adapters",
)
"""Entry-point groups discovered by the Stargraph plugin loader."""

_TRACE_ENV: str = "STARGRAPH_TRACE_PLUGINS"

_logger = get_logger("stargraph.plugin")


def _trace_enabled() -> bool:
    return bool(os.environ.get(_TRACE_ENV))


def _trace(event: str, **fields: Any) -> None:
    if _trace_enabled():
        _logger.info(event, **fields)


def build_plugin_manager() -> pluggy.PluginManager:
    """Construct a :class:`pluggy.PluginManager` for Stargraph.

    Returns an empty manager (only Stargraph's hookspecs registered) when
    no plugin distributions are installed.
    """
    pm = pluggy.PluginManager(PROJECT)
    cast("Any", pm).add_hookspecs(hookspecs)
    _trace("plugin.discovery.start", groups=GROUPS)

    # Stage 1: enumerate entry points per group, group by distribution.
    by_dist: dict[str, list[tuple[str, EntryPoint]]] = {}
    for group in GROUPS:
        for ep in entry_points(group=group):
            if ep.dist is None:
                # Detached entry points (no owning dist metadata) cannot
                # be tied back to a manifest factory.
                raise PluginLoadError(
                    f"entry point {ep.name!r} in group {group!r} has no "
                    "owning distribution; cannot validate manifest",
                    group=group,
                    name=ep.name,
                )
            by_dist.setdefault(ep.dist.name, []).append((group, ep))
            _trace(
                "plugin.discovery.entry",
                dist=ep.dist.name,
                group=group,
                name=ep.name,
            )

    # Stage 1 (cont): validate every dist's manifest before any imports.
    manifests: dict[str, PluginManifest] = {}
    claimed: dict[str, str] = {}
    for dist_name in by_dist:
        manifest = _load_and_validate_manifest(dist_name)
        _detect_namespace_conflict(manifest, dist_name, claimed)
        manifests[dist_name] = manifest
        _trace(
            "plugin.manifest.validated",
            dist=dist_name,
            api_version=manifest.api_version,
            namespaces=manifest.namespaces,
            order=manifest.order,
        )

    # Stage 2: sort by manifest.order; duplicate orders are fatal.
    order_seen: dict[int, str] = {}
    for dist_name, manifest in manifests.items():
        prior = order_seen.get(manifest.order)
        if prior is not None:
            raise PluginLoadError(
                f"plugin order collision at {manifest.order}: "
                f"{prior!r} and {dist_name!r} both declare the same load order",
                order=manifest.order,
                dists=(prior, dist_name),
            )
        order_seen[manifest.order] = dist_name

    sorted_dists = sorted(manifests, key=lambda d: manifests[d].order)

    # Stage 2 (cont): register each surviving dist's entry-point modules.
    for dist_name in sorted_dists:
        for group, ep in by_dist[dist_name]:
            module = ep.load()
            register_name = f"{dist_name}:{group}:{ep.name}"
            cast("Any", pm).register(module, name=register_name)
            _trace(
                "plugin.register",
                dist=dist_name,
                group=group,
                name=ep.name,
                register_name=register_name,
            )

    # Pick up pure-pluggy plugins that ship hookimpls under the
    # `stargraph` group itself (no Tool/Skill/Store/Pack entries).
    cast("Any", pm).load_setuptools_entrypoints(PROJECT)
    _trace("plugin.discovery.done", dist_count=len(sorted_dists))
    return pm
