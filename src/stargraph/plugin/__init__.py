# SPDX-License-Identifier: Apache-2.0
"""Public API for the Stargraph plugin system.

Re-exports the pluggy markers and the :mod:`hookspecs` module so
plugins can simply ``from stargraph.plugin import hookimpl`` or
``from stargraph.plugin import hookspecs``.
"""

from stargraph.plugin import hookspecs
from stargraph.plugin._markers import hookimpl, hookspec
from stargraph.plugin.loader import build_plugin_manager

__all__ = [
    "build_plugin_manager",
    "hookimpl",
    "hookspec",
    "hookspecs",
]
