# SPDX-License-Identifier: Apache-2.0
"""Pluggy markers for the Stargraph plugin system.

Defines the ``PROJECT`` name plus the :data:`hookspec` and
:data:`hookimpl` markers that hook specifications and implementations
must use. Centralised here so every Stargraph plugin imports the same
markers and pluggy can correctly route hook calls.
"""

from __future__ import annotations

from pluggy import HookimplMarker, HookspecMarker

PROJECT: str = "stargraph"
"""Project name shared by all Stargraph pluggy markers."""

hookspec: HookspecMarker = HookspecMarker(PROJECT)
"""Decorator for declaring a Stargraph hook specification."""

hookimpl: HookimplMarker = HookimplMarker(PROJECT)
"""Decorator plugins use to mark their hook implementations."""
