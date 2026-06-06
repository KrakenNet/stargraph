# SPDX-License-Identifier: Apache-2.0
"""Manifest factory for the ``beta`` synthetic plugin."""

from __future__ import annotations

from stargraph.ir import PluginManifest


def make_manifest() -> PluginManifest:
    """Return a fresh :class:`PluginManifest` for the beta plugin."""
    return PluginManifest(
        name="beta",
        version="0.2.0",
        api_version="1",
        namespaces=["beta"],
        provides=["skill"],
        order=200,
    )
