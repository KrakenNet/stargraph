# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.plugin._manifest` (FR-19, FR-20, AC-15.1-4).

Covers the manifest pre-validation primitives the loader relies on:

* :func:`_load_and_validate_manifest` requires the dist to expose a
  ``stargraph_plugin`` entry-point factory; it raises :class:`PluginLoadError`
  with the dist name when missing or when the factory returns a
  non-:class:`PluginManifest` value.
* :func:`_enforce_api_version` rejects manifests whose major
  ``api_version`` does not match :data:`STARGRAPH_API_VERSION_MAJOR`,
  including malformed values.
* :func:`_detect_namespace_conflict` raises with both contributing dist
  names so operators can pick the offender to uninstall.
* The manifest factory itself is zero-side-effect: calling it multiple
  times produces equal :class:`PluginManifest` instances and does not
  mutate any module-level state.
"""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import EntryPoint
from pathlib import Path
from types import ModuleType  # noqa: TC003
from typing import Any, cast
from unittest.mock import patch

import pytest

# Make ``tests/fixtures/plugins/*`` importable as top-level packages so that
# synthetic EntryPoint values like ``plugin_alpha.manifest:make_manifest``
# resolve under :func:`EntryPoint.load`.
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plugins"
if str(_FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(_FIXTURES_DIR))

from stargraph.errors import PluginLoadError  # noqa: E402
from stargraph.ir import PluginManifest  # noqa: E402
from stargraph.plugin._manifest import (  # noqa: E402
    STARGRAPH_API_VERSION_MAJOR,
    _detect_namespace_conflict,
    _enforce_api_version,
    _load_and_validate_manifest,
)

# Loaded via importlib because the ``tests/fixtures/plugins`` directory is
# only added to ``sys.path`` at runtime; a static ``import`` would not
# resolve under pyright strict.
alpha_manifest: ModuleType = importlib.import_module("plugin_alpha.manifest")


class _FakeDist:
    """Minimal stand-in for :class:`importlib.metadata.Distribution`.

    Only the ``name`` attribute is read by the Stargraph loader; everything
    else on the real ``Distribution`` API is irrelevant for stage-1
    discovery.
    """

    def __init__(self, name: str) -> None:
        self.name: str = name


def _make_ep(name: str, value: str, group: str, dist_name: str) -> EntryPoint:
    """Build a synthetic :class:`EntryPoint` bound to a fake distribution.

    Uses the private :meth:`EntryPoint._for` to attach the dist (the
    same hook stdlib uses internally when reading installed metadata).
    """
    ep = EntryPoint(name=name, value=value, group=group)
    # ``EntryPoint._for(dist)`` is a stdlib-internal hook that attaches a
    # Distribution to a synthetic EntryPoint -- the same call-site stdlib
    # uses when reading installed metadata.
    bound = cast(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        "EntryPoint",
        ep._for(_FakeDist(dist_name)),  # type: ignore[attr-defined]
    )
    return bound


def _patch_entry_points(eps: list[EntryPoint]) -> Any:
    """Return a context manager patching :func:`importlib.metadata.entry_points`.

    The Stargraph manifest module imports ``entry_points`` at module scope;
    patch the bound name on the module so calls inside
    :func:`_load_and_validate_manifest` see the synthetic data.
    """

    def fake_entry_points(*, group: str, name: str | None = None) -> list[EntryPoint]:
        out = [ep for ep in eps if ep.group == group]
        if name is not None:
            out = [ep for ep in out if ep.name == name]
        return out

    return patch("stargraph.plugin._manifest.entry_points", fake_entry_points)


# ---------------------------------------------------------------------------
# Manifest factory zero-side-effect (AC-15.1)
# ---------------------------------------------------------------------------


def test_manifest_factory_is_zero_side_effect() -> None:
    """``make_manifest`` is a pure constructor: repeated calls do not mutate state."""
    before: int = alpha_manifest.SIDE_EFFECT_COUNTER  # pyright: ignore[reportAny]
    m1: PluginManifest = alpha_manifest.make_manifest()  # pyright: ignore[reportAny]
    m2: PluginManifest = alpha_manifest.make_manifest()  # pyright: ignore[reportAny]
    after: int = alpha_manifest.SIDE_EFFECT_COUNTER  # pyright: ignore[reportAny]

    assert before == after, "factory leaked side-effects to module-level state"
    assert isinstance(m1, PluginManifest)
    assert isinstance(m2, PluginManifest)
    # Distinct instances (no caching) yet equal by value.
    assert m1 is not m2
    assert m1.model_dump() == m2.model_dump()


# ---------------------------------------------------------------------------
# _enforce_api_version (FR-20, AC-15.2)
# ---------------------------------------------------------------------------


def test_enforce_api_version_accepts_matching_major() -> None:
    """A manifest whose api_version major matches Stargraph's loads cleanly."""
    m = PluginManifest(
        name="ok",
        version="0.1",
        api_version="1",
        namespaces=["ok"],
        provides=["tool"],
        order=100,
    )
    # Should not raise.
    _enforce_api_version(m, "ok")


def test_enforce_api_version_rejects_major_mismatch() -> None:
    """``api_version="2"`` is rejected when Stargraph major is 1."""
    bad = PluginManifest.model_construct(
        name="bad",
        version="0.1",
        api_version="2",
        namespaces=["bad"],
        provides=["tool"],
        order=100,
    )
    with pytest.raises(PluginLoadError) as exc_info:
        _enforce_api_version(bad, "bad")  # pyright: ignore[reportArgumentType]
    err = exc_info.value
    assert "incompatible" in err.message
    assert err.context["dist"] == "bad"
    assert err.context["api_version"] == "2"
    assert err.context["stargraph_major"] == STARGRAPH_API_VERSION_MAJOR


def test_enforce_api_version_rejects_malformed_value() -> None:
    """Empty / non-numeric api_version surfaces as a malformed-value error."""
    bad = PluginManifest.model_construct(
        name="malformed",
        version="0.1",
        api_version="",
        namespaces=["m"],
        provides=["tool"],
        order=100,
    )
    with pytest.raises(PluginLoadError) as exc_info:
        _enforce_api_version(bad, "malformed")  # pyright: ignore[reportArgumentType]
    err = exc_info.value
    assert "malformed api_version" in err.message
    assert err.context["dist"] == "malformed"


# ---------------------------------------------------------------------------
# _detect_namespace_conflict (FR-21, AC-15.3)
# ---------------------------------------------------------------------------


def test_detect_namespace_conflict_first_dist_wins_no_op() -> None:
    """First registration of a namespace just records ownership."""
    m = PluginManifest(
        name="alpha",
        version="0.1",
        api_version="1",
        namespaces=["shared"],
        provides=["tool"],
        order=100,
    )
    claimed: dict[str, str] = {}
    _detect_namespace_conflict(m, "alpha", claimed)
    assert claimed == {"shared": "alpha"}


def test_detect_namespace_conflict_same_dist_re_registers_silently() -> None:
    """A dist re-asserting its own namespace is a no-op (re-load tolerance)."""
    m = PluginManifest(
        name="alpha",
        version="0.1",
        api_version="1",
        namespaces=["shared"],
        provides=["tool"],
        order=100,
    )
    claimed: dict[str, str] = {"shared": "alpha"}
    _detect_namespace_conflict(m, "alpha", claimed)
    assert claimed == {"shared": "alpha"}


def test_detect_namespace_conflict_two_dists_raise_with_both_names() -> None:
    """Two dists claiming the same namespace surface BOTH names in the error."""
    m_b = PluginManifest(
        name="beta",
        version="0.1",
        api_version="1",
        namespaces=["shared"],
        provides=["tool"],
        order=200,
    )
    claimed: dict[str, str] = {"shared": "alpha"}
    with pytest.raises(PluginLoadError) as exc_info:
        _detect_namespace_conflict(m_b, "beta", claimed)
    err = exc_info.value
    assert "namespace conflict" in err.message
    assert "alpha" in err.message
    assert "beta" in err.message
    assert err.context["namespace"] == "shared"
    assert set(err.context["dists"]) == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# _load_and_validate_manifest (FR-19, AC-15.1)
# ---------------------------------------------------------------------------


def test_load_and_validate_manifest_happy_path() -> None:
    """End-to-end: factory loads, returns PluginManifest, api_version OK."""
    ep = _make_ep(
        name="stargraph_plugin",
        value="plugin_alpha.manifest:make_manifest",
        group="stargraph",
        dist_name="alpha",
    )
    with _patch_entry_points([ep]):
        manifest = _load_and_validate_manifest("alpha")
    assert manifest.name == "alpha"
    assert manifest.api_version == "1"
    assert manifest.namespaces == ["alpha"]


def test_load_and_validate_manifest_missing_factory_raises() -> None:
    """Dist with plugin entries but no stargraph_plugin factory is rejected."""
    with _patch_entry_points([]), pytest.raises(PluginLoadError) as exc_info:
        _load_and_validate_manifest("alpha")
    err = exc_info.value
    assert "no stargraph_plugin" in err.message
    assert err.context["dist"] == "alpha"


def test_load_and_validate_manifest_factory_returns_non_manifest_raises() -> None:
    """Factory must return a :class:`PluginManifest`, not arbitrary objects."""
    # The fixture below is a callable the EntryPoint will resolve.
    ep = _make_ep(
        name="stargraph_plugin",
        value=f"{__name__}:_returns_dict_factory",
        group="stargraph",
        dist_name="bogus",
    )
    with _patch_entry_points([ep]), pytest.raises(PluginLoadError) as exc_info:
        _load_and_validate_manifest("bogus")
    err = exc_info.value
    assert "must return a PluginManifest" in err.message
    assert err.context["dist"] == "bogus"


def _returns_dict_factory() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
    """Test helper (loaded via EntryPoint.load): returns a non-PluginManifest."""
    return {"name": "bogus"}


def test_load_and_validate_manifest_propagates_api_version_mismatch() -> None:
    """Stage-1 also enforces api_version; mismatched major bubbles up."""
    ep = _make_ep(
        name="stargraph_plugin",
        value=f"{__name__}:_returns_v2_manifest",
        group="stargraph",
        dist_name="future",
    )
    with _patch_entry_points([ep]), pytest.raises(PluginLoadError) as exc_info:
        _load_and_validate_manifest("future")
    err = exc_info.value
    assert "incompatible" in err.message
    assert err.context["dist"] == "future"


def _returns_v2_manifest() -> PluginManifest:  # pyright: ignore[reportUnusedFunction]
    """Test helper (loaded via EntryPoint.load): future-major-version manifest."""
    return PluginManifest.model_construct(
        name="future",
        version="0.1",
        api_version="2",
        namespaces=["future"],
        provides=["tool"],
        order=100,
    )
