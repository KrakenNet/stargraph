# SPDX-License-Identifier: Apache-2.0
"""End-to-end custom store-provider registration (FR-19, FR-22, NFR-9).

Two scenarios:

* :func:`test_registry_discovery` -- a custom ``register_stores`` hookimpl
  shipped via an entry-point module surfaces in
  :meth:`StoreRegistry.list_stores` after the loader builds the plugin
  manager and aggregates the collect-all hook.
* :func:`test_namespace_conflict_loud_fail` -- two distributions
  registering a store with the same ``name`` raise
  :class:`NamespaceConflictError` (stargraph-knowledge design §4.5: name
  uniqueness is invariant; collision is loud-fail).
"""

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

# Make ``tests/fixtures/plugins/*`` importable so synthetic EntryPoint
# values like ``plugin_knowledge.manifest`` resolve under ``ep.load()``.
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plugins"
if str(_FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_DIR))

from stargraph.errors import NamespaceConflictError  # noqa: E402
from stargraph.ir import StoreSpec  # noqa: E402
from stargraph.plugin._markers import hookimpl  # noqa: E402
from stargraph.plugin.loader import build_plugin_manager  # noqa: E402
from stargraph.registry import StoreRegistry  # noqa: E402

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class _FakeDist:
    """Minimal :class:`importlib.metadata.Distribution` stand-in."""

    def __init__(self, name: str) -> None:
        self.name: str = name


def _ep(name: str, value: str, group: str, dist_name: str) -> EntryPoint:
    """Build an :class:`EntryPoint` bound to a synthetic distribution."""
    ep = EntryPoint(name=name, value=value, group=group)
    bound = cast(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        "EntryPoint",
        ep._for(_FakeDist(dist_name)),  # type: ignore[attr-defined]
    )
    return bound


def _patch_eps(eps: list[EntryPoint]) -> Any:
    """Patch ``entry_points`` on both loader and manifest modules."""

    def fake_entry_points(*, group: str, name: str | None = None) -> list[EntryPoint]:
        out = [ep for ep in eps if ep.group == group]
        if name is not None:
            out = [ep for ep in out if ep.name == name]
        return out

    return _StackedPatch(
        patch("stargraph.plugin.loader.entry_points", fake_entry_points),
        patch("stargraph.plugin._manifest.entry_points", fake_entry_points),
    )


class _StackedPatch:
    """Helper context manager stacking two ``mock.patch`` objects."""

    def __init__(self, *patches: Any) -> None:
        self._patches: tuple[Any, ...] = patches

    def __enter__(self) -> _StackedPatch:
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in reversed(self._patches):
            p.__exit__(*exc)


def _aggregate_stores_into(pm: Any, registry: StoreRegistry, *, owner: str) -> None:
    """Drive the ``register_stores`` collect-all hook into ``registry``.

    Pluggy's hook caller returns one ``list[StoreSpec]`` per registered
    plugin (in registration order). Each list is registered under the
    same ``owner`` -- production code threads dist names per-plugin; the
    test harness uses a single owner per call so the namespace-conflict
    path can be exercised cleanly.
    """
    results: list[list[StoreSpec]] = pm.hook.register_stores()
    for store_list in results:
        for spec in store_list:
            registry.register(spec, owner=owner)


# ---------------------------------------------------------------------------
# test_registry_discovery (FR-19)
# ---------------------------------------------------------------------------


def test_registry_discovery() -> None:
    """A custom ``register_stores`` hookimpl appears in ``list_stores()``."""
    eps = [
        _ep(
            "stargraph_plugin",
            "plugin_knowledge.manifest:make_manifest",
            "stargraph",
            "plugin_knowledge",
        ),
        # The manifest module *itself* carries the @hookimpl-decorated
        # ``register_stores`` -- registering it as a stargraph.stores
        # entry-point lets pluggy discover the hookimpl during stage 2.
        _ep(
            "stores",
            "plugin_knowledge.manifest",
            "stargraph.stores",
            "plugin_knowledge",
        ),
    ]
    with _patch_eps(eps):
        pm = build_plugin_manager()

    registry = StoreRegistry()
    _aggregate_stores_into(pm, registry, owner="plugin_knowledge")

    stores = registry.list_stores()
    assert len(stores) == 1
    spec = stores[0]
    assert spec.name == "knowledge.demo.vectors"
    assert spec.provider == "lancedb"
    assert spec.protocol == "vector"


# ---------------------------------------------------------------------------
# test_namespace_conflict_loud_fail (FR-19, NFR-9)
# ---------------------------------------------------------------------------


def test_namespace_conflict_loud_fail() -> None:
    """Two providers claiming the same store name raise ``NamespaceConflictError``."""
    registry = StoreRegistry()
    spec_a = StoreSpec(
        name="db.shared",
        provider="lancedb",
        protocol="vector",
        config_schema={},
        capabilities=[],
    )
    spec_b = StoreSpec(
        name="db.shared",
        provider="ryugraph",
        protocol="graph",
        config_schema={},
        capabilities=[],
    )
    registry.register(spec_a, owner="dist_alpha")
    with pytest.raises(NamespaceConflictError) as exc_info:
        registry.register(spec_b, owner="dist_beta")
    err = exc_info.value
    assert err.context["namespace"] == "db.shared"
    assert err.context["existing_owner"] == "dist_alpha"
    assert err.context["new_owner"] == "dist_beta"
    assert "db.shared" in err.message
    assert "dist_alpha" in err.message
    assert "dist_beta" in err.message


def test_namespace_conflict_via_pluggy_aggregation() -> None:
    """Two pluggy plugins both contributing the same store name are loud-fail."""

    class _PluginX:
        @hookimpl
        def register_stores(self) -> list[StoreSpec]:
            return [
                StoreSpec(
                    name="db.shared",
                    provider="lancedb",
                    protocol="vector",
                    config_schema={},
                    capabilities=[],
                ),
            ]

    class _PluginY:
        @hookimpl
        def register_stores(self) -> list[StoreSpec]:
            return [
                StoreSpec(
                    name="db.shared",
                    provider="ryugraph",
                    protocol="graph",
                    config_schema={},
                    capabilities=[],
                ),
            ]

    with _patch_eps([]):
        pm = build_plugin_manager()
    cast("Any", pm).register(_PluginX(), name="plugin_x")
    cast("Any", pm).register(_PluginY(), name="plugin_y")

    registry = StoreRegistry()
    # First plugin's contribution registers cleanly under owner ``x``...
    results: list[list[StoreSpec]] = pm.hook.register_stores()
    # Pluggy returns hookimpl results in LIFO registration order: y, x.
    assert len(results) == 2
    registry.register(results[0][0], owner="plugin_y")
    with pytest.raises(NamespaceConflictError):
        registry.register(results[1][0], owner="plugin_x")
