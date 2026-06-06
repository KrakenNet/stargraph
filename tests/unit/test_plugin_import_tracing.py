# SPDX-License-Identifier: Apache-2.0
"""Cold-import tracing for the Stargraph plugin loader (NFR-7).

NFR-7: stage-1 manifest validation must NOT import any tool / skill /
store / pack module from any plugin distribution. Importing them is
deferred to stage-2 registration so the loader can reject incompatible
plugins before paying their import cost (and before any plugin code
gets a chance to run).

Strategy:

* CPython's :func:`sys.addaudithook` only fires for top-level ``import``
  statements -- it does NOT fire for :func:`importlib.import_module`,
  which is what :class:`importlib.metadata.EntryPoint` uses internally.
  We instead trace imports by wrapping :data:`sys.modules` with a
  recording :class:`dict` subclass that captures every ``__setitem__``
  call. Module objects are inserted into ``sys.modules`` exactly once
  (the moment after their finder/loader resolves them and before their
  body executes), so a recording dict gives us a precise, deterministic
  ordered timeline of import events.
* The synthetic plugins (``plugin_alpha`` and ``plugin_beta``) keep
  their manifest factory in a module distinct from their tool / skill
  modules so we can tell stages apart.
* Stage-1 imports only the manifest module; stage-2 imports the
  tool/skill module. The recording dict's timeline must show every
  manifest module BEFORE every tool/skill module.

The companion ``test_aborted_load_never_imports_tool_modules`` exercises
the stronger property: when stage-1 raises, stage-2 never runs and the
tool/skill modules stay cold (no entries in ``sys.modules``).
"""

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

# Make synthetic plugin packages importable as top-level names.
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plugins"
if str(_FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_DIR))

from stargraph.errors import PluginLoadError  # noqa: E402
from stargraph.ir import PluginManifest  # noqa: E402, TC001
from stargraph.plugin.loader import build_plugin_manager  # noqa: E402

# Modules whose import we want to monitor. These names match the values
# in the synthetic EntryPoints below.
_TOOL_MODULES: tuple[str, ...] = ("plugin_alpha.tools", "plugin_beta.skills")
_MANIFEST_MODULES: tuple[str, ...] = ("plugin_alpha.manifest", "plugin_beta.manifest")
_ALL_FIXTURE_PKGS: tuple[str, ...] = (
    "plugin_alpha",
    "plugin_alpha.manifest",
    "plugin_alpha.tools",
    "plugin_beta",
    "plugin_beta.manifest",
    "plugin_beta.skills",
)


class _FakeDist:
    """Minimal :class:`importlib.metadata.Distribution` stand-in."""

    def __init__(self, name: str) -> None:
        self.name: str = name


def _ep(name: str, value: str, group: str, dist: str) -> EntryPoint:
    """Build an :class:`EntryPoint` bound to a fake distribution."""
    ep = EntryPoint(name=name, value=value, group=group)
    # See ``test_plugin_manifest._make_ep`` for the stdlib-internal hook rationale.
    bound = cast(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        "EntryPoint",
        ep._for(_FakeDist(dist)),  # type: ignore[attr-defined]
    )
    return bound


def _patch_eps(eps: list[EntryPoint]) -> Any:
    """Patch ``entry_points`` on both loader and manifest modules."""

    def fake(*, group: str, name: str | None = None) -> list[EntryPoint]:
        out = [e for e in eps if e.group == group]
        if name is not None:
            out = [e for e in out if e.name == name]
        return out

    return _StackedPatch(
        patch("stargraph.plugin.loader.entry_points", fake),
        patch("stargraph.plugin._manifest.entry_points", fake),
    )


class _StackedPatch:
    """Context manager stacking two ``mock.patch`` instances."""

    def __init__(self, *patches: Any) -> None:
        self._patches: tuple[Any, ...] = patches

    def __enter__(self) -> _StackedPatch:
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in reversed(self._patches):
            p.__exit__(*exc)


class _ImportTracer:
    """Records every fresh insertion into ``sys.modules`` for a watched set.

    Active inside the ``with`` block. Wraps :data:`sys.modules` by
    monkey-patching :meth:`dict.__setitem__` via a sidecar tracker --
    Python's :data:`sys.modules` cannot itself be replaced wholesale
    while imports are in flight, but we can patch ``importlib._bootstrap``'s
    ``_call_with_frames_removed`` path indirectly by hooking the
    finder's :meth:`_init_module_attrs` -- too invasive. The simplest
    portable hook is :class:`importlib.abc.MetaPathFinder`-based
    instrumentation, but because we control the test entry point we can
    take the cheapest path: snapshot ``sys.modules`` before and after,
    plus an explicit poll of intermediate state via a metapath finder
    that records the order in which the import system asks about each
    module.

    Implementation notes
    --------------------
    The recorder installs a :class:`MetaPathFinder` at index 0 of
    :data:`sys.meta_path`. The finder records every ``find_spec(name,
    ...)`` call for names in ``watch`` and then returns ``None`` so the
    real finder chain handles loading. Because :func:`importlib.import_module`
    triggers ``find_spec`` exactly once per module (in submodule order
    parent -> child), the call order yields a deterministic timeline
    of which module the import system was asked about first.
    """

    def __init__(self, watch: set[str]) -> None:
        self._watch: set[str] = set(watch)
        self.events: list[str] = []
        self._finder: Any = None

    def __enter__(self) -> _ImportTracer:
        events = self.events
        watch = self._watch

        class _RecordingFinder:
            @staticmethod
            def find_spec(name: str, path: object = None, target: object = None) -> None:
                if name in watch:
                    events.append(name)
                return None

        self._finder = _RecordingFinder
        sys.meta_path.insert(0, self._finder)
        return self

    def __exit__(self, *exc: object) -> None:
        sys.meta_path.remove(self._finder)


@pytest.fixture(autouse=True)
def _purge_synthetic_modules() -> Any:  # pyright: ignore[reportUnusedFunction]
    """Evict synthetic plugin modules from ``sys.modules`` between tests."""
    for name in _ALL_FIXTURE_PKGS:
        sys.modules.pop(name, None)
    yield
    for name in _ALL_FIXTURE_PKGS:
        sys.modules.pop(name, None)


def _two_dists_eps() -> list[EntryPoint]:
    """Build the canonical two-dist EntryPoint list used by every test."""
    return [
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


# ---------------------------------------------------------------------------
# NFR-7 -- the headline test
# ---------------------------------------------------------------------------


def test_no_tool_module_imported_during_stage1_manifest_validation() -> None:
    """Tool / skill / store / pack modules import ONLY during stage-2.

    Every manifest module's first ``find_spec`` call must precede every
    tool/skill module's first ``find_spec`` call. This proves stage-1
    manifest validation is import-cold for tool code (NFR-7).
    """
    # Sanity: the autouse fixture left no synthetic modules cached.
    for name in _ALL_FIXTURE_PKGS:
        assert name not in sys.modules, f"{name} leaked from a prior test"

    watch = set(_TOOL_MODULES) | set(_MANIFEST_MODULES)
    eps = _two_dists_eps()
    with _ImportTracer(watch) as tracer, _patch_eps(eps):
        build_plugin_manager()

    timeline = tracer.events

    def _first(name: str) -> int:
        for i, ev in enumerate(timeline):
            if ev == name:
                return i
        return -1

    alpha_manifest_idx = _first("plugin_alpha.manifest")
    alpha_tools_idx = _first("plugin_alpha.tools")
    beta_manifest_idx = _first("plugin_beta.manifest")
    beta_skills_idx = _first("plugin_beta.skills")

    assert alpha_manifest_idx >= 0, f"alpha manifest never traced: {timeline}"
    assert alpha_tools_idx >= 0, f"alpha tools never traced: {timeline}"
    assert beta_manifest_idx >= 0, f"beta manifest never traced: {timeline}"
    assert beta_skills_idx >= 0, f"beta skills never traced: {timeline}"

    # NFR-7: every manifest finder-call precedes every tool/skill finder-call.
    last_manifest = max(alpha_manifest_idx, beta_manifest_idx)
    first_tool = min(alpha_tools_idx, beta_skills_idx)
    assert last_manifest < first_tool, (
        f"NFR-7 violation: a tool/skill module was discovered before all "
        f"manifests had been validated. last_manifest={last_manifest} "
        f"first_tool={first_tool} timeline={timeline}"
    )


def test_tool_modules_are_in_sys_modules_after_stage2() -> None:
    """Stage-2 actually imports each tool module (sys.modules has them)."""
    for name in _ALL_FIXTURE_PKGS:
        assert name not in sys.modules

    eps = _two_dists_eps()
    with _patch_eps(eps):
        build_plugin_manager()

    for name in _TOOL_MODULES:
        assert name in sys.modules, f"stage-2 should have imported {name}"


def test_aborted_load_never_imports_tool_modules() -> None:
    """If stage-1 raises, stage-2 never runs and tool modules stay cold.

    Replace beta's manifest factory with one that raises immediately.
    Even though alpha's manifest validates cleanly, the loader must
    fail BEFORE entering stage-2; therefore neither alpha's tools nor
    beta's skills should land in ``sys.modules`` (NFR-7).
    """
    for name in _ALL_FIXTURE_PKGS:
        assert name not in sys.modules

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
            f"{__name__}:_factory_that_raises",
            "stargraph",
            "beta",
        ),
    ]
    with _patch_eps(eps), pytest.raises(PluginLoadError):
        build_plugin_manager()

    assert "plugin_alpha.tools" not in sys.modules, (
        "NFR-7 violation: stage-1 abort still imported a tool module"
    )
    assert "plugin_beta.skills" not in sys.modules, (
        "NFR-7 violation: stage-1 abort still imported a skill module"
    )


def _factory_that_raises() -> PluginManifest:  # pyright: ignore[reportUnusedFunction]
    """Test helper (loaded via EntryPoint.load): always raises PluginLoadError."""
    raise PluginLoadError("synthetic stage-1 failure", dist="beta")


def test_import_tracer_records_only_watched_modules() -> None:
    """The tracer is selective: unwatched modules do not appear in events."""
    import importlib

    with _ImportTracer({"plugin_alpha.manifest"}) as tracer:
        # Force a fresh import of the manifest module via importlib so
        # pyright does not need to resolve the runtime-injected
        # ``plugin_alpha`` package statically.
        sys.modules.pop("plugin_alpha.manifest", None)
        sys.modules.pop("plugin_alpha", None)
        importlib.import_module("plugin_alpha.manifest")

    assert "plugin_alpha.manifest" in tracer.events
    # No noise from the parent or sibling modules:
    assert "plugin_alpha" not in tracer.events
    assert "plugin_alpha.tools" not in tracer.events
