# SPDX-License-Identifier: Apache-2.0
"""stargraph.registry -- in-memory tool/skill registry (FR-23, design §3.5).

Re-exports :class:`ToolRegistry` (the in-memory dict-backed registry that
the foundation's pluggy loader populates via ``register_tools`` /
``register_skills`` hooks). The SQLite-backed cache mentioned in design
§3.5 is deferred (Open Q5 resolution: in-memory dict for v1).
"""

from __future__ import annotations

from stargraph.registry.stores import StoreRegistry
from stargraph.registry.tools import Tool, ToolRegistry

__all__ = ["StoreRegistry", "Tool", "ToolRegistry"]
