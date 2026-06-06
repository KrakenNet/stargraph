# SPDX-License-Identifier: Apache-2.0
"""Manifest discovery and validation for Stargraph plugins.

A Stargraph plugin distribution exposes a single ``stargraph_plugin`` entry
point in the ``stargraph`` group whose value is a zero-arg callable
returning a :class:`stargraph.ir.PluginManifest`. The loader imports only
this manifest factory before deciding whether to register the dist's
tool/skill/store/pack entry points.

This split is the same pattern Datasette uses (datasette/plugins.py:
``check_version``) and lets Stargraph reject incompatible plugins or
resolve namespace conflicts without paying the import cost of every
tool module.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from stargraph.errors import PluginLoadError
from stargraph.ir import PluginManifest

__all__ = [
    "STARGRAPH_API_VERSION_MAJOR",
    "_detect_namespace_conflict",
    "_enforce_api_version",
    "_load_and_validate_manifest",
]

STARGRAPH_API_VERSION_MAJOR: int = 1
"""Major version of the Stargraph plugin API. Manifests must match."""


def _load_and_validate_manifest(dist_name: str) -> PluginManifest:
    """Load the ``stargraph_plugin`` manifest factory for ``dist_name``.

    Raises :class:`PluginLoadError` if the dist declares plugin entry
    points but no ``stargraph_plugin`` factory, if the factory does not
    return a :class:`PluginManifest`, or if the manifest's
    ``api_version`` is incompatible with this Stargraph major.
    """
    eps = entry_points(group="stargraph", name="stargraph_plugin")
    matches = [ep for ep in eps if ep.dist is not None and ep.dist.name == dist_name]
    if not matches:
        raise PluginLoadError(
            f"{dist_name}: declares plugin entries but no stargraph_plugin "
            "manifest factory in the 'stargraph' entry-point group",
            dist=dist_name,
        )
    factory: Any = matches[0].load()
    manifest = factory()
    if not isinstance(manifest, PluginManifest):
        raise PluginLoadError(
            f"{dist_name}: stargraph_plugin factory must return a PluginManifest "
            f"(got {type(manifest).__name__!r})",
            dist=dist_name,
        )
    _enforce_api_version(manifest, dist_name)
    return manifest


def _enforce_api_version(manifest: PluginManifest, dist_name: str) -> None:
    """Reject manifests whose ``api_version`` major does not match Stargraph."""
    declared = manifest.api_version
    try:
        major = int(declared.split(".", 1)[0])
    except (ValueError, IndexError) as exc:
        raise PluginLoadError(
            f"{dist_name}: malformed api_version {declared!r}",
            dist=dist_name,
            api_version=declared,
        ) from exc
    if major != STARGRAPH_API_VERSION_MAJOR:
        raise PluginLoadError(
            f"{dist_name}: api_version {declared!r} incompatible with "
            f"Stargraph major {STARGRAPH_API_VERSION_MAJOR}. Refusing to load.",
            dist=dist_name,
            api_version=declared,
            stargraph_major=STARGRAPH_API_VERSION_MAJOR,
        )


def _detect_namespace_conflict(
    manifest: PluginManifest,
    dist_name: str,
    claimed: dict[str, str],
) -> None:
    """Update ``claimed`` with ``manifest.namespaces``, raising on conflict.

    ``claimed`` maps namespace -> owning dist. On collision both dist
    names are surfaced in the :class:`PluginLoadError` so operators can
    pick the offender to uninstall.
    """
    for ns in manifest.namespaces:
        prior = claimed.get(ns)
        if prior is not None and prior != dist_name:
            raise PluginLoadError(
                f"namespace conflict: {ns!r} claimed by both {prior!r} and {dist_name!r}",
                namespace=ns,
                dists=(prior, dist_name),
            )
        claimed[ns] = dist_name
