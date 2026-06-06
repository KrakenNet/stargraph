# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.plugin.loader` (FR-19, FR-22, AC-15.4-6).

The loader runs in two stages:

1. Discovery + manifest validation: enumerate every entry-point in
   ``stargraph.{tools,skills,stores,packs}``; group by owning distribution;
   load and validate each dist's ``stargraph_plugin`` manifest factory
   without importing tool / skill / store / pack code.
2. Registration: sort surviving dists by ``manifest.order`` (ties =
   :class:`PluginLoadError`); call ``ep.load()`` and ``pm.register(...)``
   for each entry-point module.

These tests exercise the loader against synthetic distributions whose
EntryPoints are injected by patching :func:`importlib.metadata.entry_points`
on both the loader module and the manifest module. The fixtures in
``tests/fixtures/plugins/plugin_{alpha,beta}`` supply a real importable
manifest factory plus a tools / skills module so stage-2 registration
exercises a real ``ep.load()``.
"""

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pluggy
import pytest

# Ensure ``plugin_alpha`` / ``plugin_beta`` resolve as top-level packages so
# synthetic EntryPoint values like ``"plugin_alpha.tools"`` load cleanly.
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plugins"
if str(_FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_DIR))

from stargraph.errors import PluginLoadError  # noqa: E402
from stargraph.ir import PluginManifest  # noqa: E402
from stargraph.plugin.loader import GROUPS, build_plugin_manager  # noqa: E402


class _FakeDist:
    """Minimal :class:`importlib.metadata.Distribution` stand-in (loader-only)."""

    def __init__(self, name: str) -> None:
        self.name: str = name


def _ep(name: str, value: str, group: str, dist_name: str | None) -> EntryPoint:
    """Build an :class:`EntryPoint`, optionally bound to a fake distribution."""
    ep = EntryPoint(name=name, value=value, group=group)
    if dist_name is None:
        return ep
    # ``EntryPoint._for(dist)`` is a stdlib-internal hook that attaches a
    # Distribution to a synthetic EntryPoint -- the same call-site stdlib
    # uses when reading installed metadata.
    bound = cast(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        "EntryPoint",
        ep._for(_FakeDist(dist_name)),  # type: ignore[attr-defined]
    )
    return bound


def _patch_eps(eps: list[EntryPoint]) -> Any:
    """Patch ``entry_points`` on both loader and manifest modules.

    Returns a context manager that stacks two ``unittest.mock`` patches.
    The loader uses ``entry_points(group=...)`` for plugin-group
    discovery; the manifest module uses ``entry_points(group="stargraph",
    name="stargraph_plugin")`` for factory lookup. Both must see the same
    synthetic data.
    """

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


# ---------------------------------------------------------------------------
# Empty / clean cases
# ---------------------------------------------------------------------------


def test_build_plugin_manager_with_no_entry_points() -> None:
    """No installed plugins -> manager is built with only Stargraph's hookspecs."""
    with _patch_eps([]):
        pm = build_plugin_manager()
    assert isinstance(pm, pluggy.PluginManager)
    # Only the hookspecs module is registered (no third-party plugins).
    plugin_names = {pm.get_name(p) for p in pm.get_plugins()}
    # The hookspecs module is registered via ``add_hookspecs`` -- it has
    # no plugin-name binding so plugin_names is empty for synthetic envs.
    assert plugin_names == set()


def test_build_plugin_manager_happy_path_two_dists() -> None:
    """Two dists with non-conflicting namespaces register both modules."""
    eps = [
        _ep(
            "stargraph_plugin",
            "plugin_alpha.manifest:make_manifest",
            "stargraph",
            "alpha",
        ),
        _ep("alpha_tools", "plugin_alpha.tools", "stargraph.tools", "alpha"),
        _ep(
            "stargraph_plugin",
            "plugin_beta.manifest:make_manifest",
            "stargraph",
            "beta",
        ),
        _ep("beta_skills", "plugin_beta.skills", "stargraph.skills", "beta"),
    ]
    with _patch_eps(eps):
        pm = build_plugin_manager()

    plugin_names = {pm.get_name(p) for p in pm.get_plugins()}
    assert "alpha:stargraph.tools:alpha_tools" in plugin_names
    assert "beta:stargraph.skills:beta_skills" in plugin_names


def test_build_plugin_manager_orders_by_manifest_order() -> None:
    """Stage-2 registration order respects ``manifest.order`` ascending.

    Construct two manifests with explicit orders and assert the
    registration sequence (visible via ``pm.get_plugins()``) follows
    ``order=100`` (alpha) before ``order=200`` (beta).
    """
    eps = [
        _ep("alpha_tools", "plugin_alpha.tools", "stargraph.tools", "alpha"),
        _ep(
            "stargraph_plugin",
            "plugin_alpha.manifest:make_manifest",
            "stargraph",
            "alpha",
        ),
        _ep("beta_skills", "plugin_beta.skills", "stargraph.skills", "beta"),
        _ep(
            "stargraph_plugin",
            "plugin_beta.manifest:make_manifest",
            "stargraph",
            "beta",
        ),
    ]
    # Spy on pluggy's ``register`` to capture exact registration order.
    registered: list[str] = []
    real_register = pluggy.PluginManager.register

    def _spy_register(self: pluggy.PluginManager, plugin: Any, name: str | None = None) -> Any:
        if name is not None:
            registered.append(name)
        return real_register(self, plugin, name=name)

    with _patch_eps(eps), patch.object(pluggy.PluginManager, "register", _spy_register):
        build_plugin_manager()

    # Filter to our synthetic registrations (ignore any pluggy plumbing).
    synthetic = [n for n in registered if n.startswith(("alpha:", "beta:"))]
    alpha_idx = next(i for i, n in enumerate(synthetic) if n.startswith("alpha:"))
    beta_idx = next(i for i, n in enumerate(synthetic) if n.startswith("beta:"))
    assert alpha_idx < beta_idx, (
        f"alpha (order=100) must register before beta (order=200): {synthetic}"
    )


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_build_plugin_manager_raises_on_detached_entry_point() -> None:
    """An entry point with ``dist=None`` is fatal (no manifest can be tied)."""
    eps = [
        _ep("orphan_tools", "plugin_alpha.tools", "stargraph.tools", None),
    ]
    with _patch_eps(eps), pytest.raises(PluginLoadError) as exc_info:
        build_plugin_manager()
    err = exc_info.value
    assert "no owning distribution" in err.message
    assert err.context["group"] == "stargraph.tools"


def test_build_plugin_manager_raises_on_namespace_conflict() -> None:
    """Two dists claiming the same namespace abort the load (FR-21)."""
    eps = [
        _ep("alpha_tools", "plugin_alpha.tools", "stargraph.tools", "alpha"),
        _ep(
            "stargraph_plugin",
            "plugin_alpha.manifest:make_manifest",
            "stargraph",
            "alpha",
        ),
        _ep("beta_skills", "plugin_beta.skills", "stargraph.skills", "beta"),
        # Force beta to claim the same namespace as alpha by pointing its
        # manifest factory at a helper that returns a manifest with
        # ``namespaces=["alpha"]``.
        _ep(
            "stargraph_plugin",
            f"{__name__}:_make_beta_manifest_claiming_alpha_ns",
            "stargraph",
            "beta",
        ),
    ]
    with _patch_eps(eps), pytest.raises(PluginLoadError) as exc_info:
        build_plugin_manager()
    err = exc_info.value
    assert "namespace conflict" in err.message
    # Both dist names appear in the error message and context.
    assert err.context["namespace"] == "alpha"
    assert set(err.context["dists"]) == {"alpha", "beta"}


def _make_beta_manifest_claiming_alpha_ns() -> PluginManifest:  # pyright: ignore[reportUnusedFunction]
    """Test helper (loaded via EntryPoint.load): claims alpha's namespace."""
    return PluginManifest(
        name="beta",
        version="0.2.0",
        api_version="1",
        namespaces=["alpha"],
        provides=["skill"],
        order=200,
    )


def test_build_plugin_manager_raises_on_order_collision() -> None:
    """Two dists declaring the same ``manifest.order`` is a fatal error."""
    eps = [
        _ep("alpha_tools", "plugin_alpha.tools", "stargraph.tools", "alpha"),
        _ep(
            "stargraph_plugin",
            "plugin_alpha.manifest:make_manifest",
            "stargraph",
            "alpha",
        ),
        _ep("beta_skills", "plugin_beta.skills", "stargraph.skills", "beta"),
        _ep(
            "stargraph_plugin",
            f"{__name__}:_make_beta_manifest_with_alpha_order",
            "stargraph",
            "beta",
        ),
    ]
    with _patch_eps(eps), pytest.raises(PluginLoadError) as exc_info:
        build_plugin_manager()
    err = exc_info.value
    assert "order collision" in err.message
    assert err.context["order"] == 100
    assert set(err.context["dists"]) == {"alpha", "beta"}


def _make_beta_manifest_with_alpha_order() -> PluginManifest:  # pyright: ignore[reportUnusedFunction]
    """Test helper (loaded via EntryPoint.load): shares alpha's ``order=100``."""
    return PluginManifest(
        name="beta",
        version="0.2.0",
        api_version="1",
        namespaces=["beta"],
        provides=["skill"],
        order=100,
    )


def test_build_plugin_manager_raises_on_api_version_mismatch() -> None:
    """A future-major manifest aborts the entire load."""
    eps = [
        _ep("future_tools", "plugin_alpha.tools", "stargraph.tools", "future"),
        _ep(
            "stargraph_plugin",
            f"{__name__}:_make_v2_manifest",
            "stargraph",
            "future",
        ),
    ]
    with _patch_eps(eps), pytest.raises(PluginLoadError) as exc_info:
        build_plugin_manager()
    err = exc_info.value
    assert "incompatible" in err.message
    assert err.context["dist"] == "future"


def _make_v2_manifest() -> PluginManifest:  # pyright: ignore[reportUnusedFunction]
    """Test helper (loaded via EntryPoint.load): ``api_version='2'`` (future)."""
    return PluginManifest.model_construct(
        name="future",
        version="9.0",
        api_version="2",
        namespaces=["future"],
        provides=["tool"],
        order=300,
    )


# ---------------------------------------------------------------------------
# Sanity: the loader knows about all five plugin groups
# ---------------------------------------------------------------------------


def test_groups_constant_covers_all_six_plugin_kinds() -> None:
    """``GROUPS`` is the source of truth for stage-1 enumeration."""
    assert set(GROUPS) == {
        "stargraph.tools",
        "stargraph.skills",
        "stargraph.stores",
        "stargraph.packs",
        "stargraph.triggers",
        "stargraph.mcp_adapters",
    }


# ---------------------------------------------------------------------------
# Trace logging path (smoke -- just ensures the env-gated path runs)
# ---------------------------------------------------------------------------


def test_build_plugin_manager_trace_logging_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ``STARGRAPH_TRACE_PLUGINS`` must not change behaviour, only logs.

    Exercises the ``_trace`` branch where the env var is truthy so the
    coverage tool does not flag the logging arm as dead.
    """
    monkeypatch.setenv("STARGRAPH_TRACE_PLUGINS", "1")
    eps = [
        _ep("alpha_tools", "plugin_alpha.tools", "stargraph.tools", "alpha"),
        _ep(
            "stargraph_plugin",
            "plugin_alpha.manifest:make_manifest",
            "stargraph",
            "alpha",
        ),
    ]
    with _patch_eps(eps):
        pm = build_plugin_manager()
    plugin_names = {pm.get_name(p) for p in pm.get_plugins()}
    assert "alpha:stargraph.tools:alpha_tools" in plugin_names
