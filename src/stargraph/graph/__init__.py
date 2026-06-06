# SPDX-License-Identifier: Apache-2.0
"""stargraph.graph — Graph definition + GraphRun handle + structural hashing.

Re-exports :class:`Graph` (sync construction; FR-1), :class:`GraphRun` (async
execution handle; FR-1, US-2, design §3.1.1) and the lifecycle-state literal
:data:`RunState` (design §3.1.3), plus the JCS structural hash and the runtime
hash from :mod:`stargraph.graph.hash` (FR-4).
"""

from __future__ import annotations

from .definition import Graph
from .hash import runtime_hash, structural_hash
from .run import GraphRun, RunState

__all__ = ["Graph", "GraphRun", "RunState", "runtime_hash", "structural_hash"]
