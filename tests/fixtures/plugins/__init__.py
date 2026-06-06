# SPDX-License-Identifier: Apache-2.0
"""Synthetic Stargraph plugin packages used by ``tests/unit/test_plugin_*``.

Each submodule under :mod:`tests.fixtures.plugins` simulates a third-party
distribution that exposes a ``stargraph_plugin`` manifest factory and one or
more entry-point modules under ``stargraph.tools`` / ``stargraph.skills`` /
``stargraph.stores`` / ``stargraph.packs``. The plugin loader tests construct
synthetic :class:`importlib.metadata.EntryPoint` records pointing at
these modules and inject them via monkey-patching
:func:`importlib.metadata.entry_points`.

The fixtures intentionally keep the manifest factory module
(``manifest.py``) free of any tool/skill imports so the loader tests can
assert that stage-1 manifest validation does not trigger import of any
tool/skill/store/pack module (NFR-7).
"""
