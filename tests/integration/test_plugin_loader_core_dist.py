# SPDX-License-Identifier: Apache-2.0
"""P0.5 regression: the plugin loader must not choke on Stargraph itself.

The core ``stargraph`` distribution declares entry points in discovered
groups (``stargraph.triggers``) but ships no ``stargraph_plugin`` manifest
factory -- before the core-dist skip, :func:`build_plugin_manager` raised
``PluginLoadError: stargraph: declares plugin entries but no
stargraph_plugin manifest factory`` on every real install. This test runs
against the *live* installed environment, so it fails if that landmine
ever returns.
"""

from __future__ import annotations

import pytest

from stargraph.plugin.loader import build_plugin_manager

pytestmark = pytest.mark.integration


def test_build_plugin_manager_skips_core_dist_on_live_install() -> None:
    pm = build_plugin_manager()
    # No plugin registered under the core dist's name prefix.
    names = [pm.get_name(p) for p in pm.get_plugins()]
    assert not any(str(n).startswith("stargraph:") for n in names), names
