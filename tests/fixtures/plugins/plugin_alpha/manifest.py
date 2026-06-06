# SPDX-License-Identifier: Apache-2.0
"""Manifest factory for the ``alpha`` synthetic plugin.

Imports nothing from the sibling ``tools`` module so the loader's
stage-1 validation cannot accidentally pull tool code into ``sys.modules``
(NFR-7).
"""

from __future__ import annotations

from stargraph.ir import PluginManifest

# Module-level counter that the manifest-factory zero-side-effect test
# inspects: the factory must be a pure constructor of a fresh
# PluginManifest. Importing the manifest module is permitted (this is
# the *one* import the loader's stage-1 pays for); calling the factory
# is what must be side-effect-free beyond returning the manifest.
SIDE_EFFECT_COUNTER: int = 0


def make_manifest() -> PluginManifest:
    """Return a fresh :class:`PluginManifest` for the alpha plugin."""
    return PluginManifest(
        name="alpha",
        version="0.1.0",
        api_version="1",
        namespaces=["alpha"],
        provides=["tool"],
        order=100,
    )
