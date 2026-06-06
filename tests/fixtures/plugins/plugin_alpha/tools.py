# SPDX-License-Identifier: Apache-2.0
"""Tool module for the ``alpha`` synthetic plugin.

Loaded during stage 2 of :func:`stargraph.plugin.build_plugin_manager`.
Stage-1 manifest validation must NOT import this module (NFR-7).
"""

from __future__ import annotations

# Sentinel set on import so import-tracing tests can detect when this
# module enters ``sys.modules``.
LOADED: bool = True


def register_tools() -> list[object]:
    """Hookimpl-shaped function returning an empty tool list."""
    return []
