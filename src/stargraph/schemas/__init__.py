# SPDX-License-Identifier: Apache-2.0
"""Schema helpers: canonical URLs and on-disk paths for Stargraph IR JSON Schemas."""

from __future__ import annotations

from pathlib import Path

__all__ = ["schema_path", "schema_url"]


def schema_url(version: str = "v1") -> str:
    """Return the canonical ``$id`` URL for the IR JSON Schema at *version*."""
    return f"https://stargraph.dev/schemas/ir-{version}.json"


def schema_path(version: str = "v1") -> Path:
    """Return the on-disk :class:`Path` to the IR JSON Schema at *version*."""
    return Path(__file__).parent / f"ir-{version}.json"
