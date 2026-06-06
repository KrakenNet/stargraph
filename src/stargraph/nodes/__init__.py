# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes -- executable graph-node hierarchy (FR-1, design §5).

Phase 1 ships the abstract :class:`NodeBase` plus the :class:`EchoNode`
fixture stub. Concrete production nodes -- :class:`DSPyNode` (FR-5),
:class:`SubGraphNode` (FR-7), :class:`MLNode` (FR-30) -- land in
subsequent tasks (1.30+, Phase 2/3) and will be re-exported from this
namespace per design §5.
"""

from __future__ import annotations

from stargraph.nodes.base import EchoNode, ExecutionContext, NodeBase
from stargraph.nodes.dspy import DSPyNode
from stargraph.nodes.subgraph import SubGraphNode

__all__ = [
    "DSPyNode",
    "EchoNode",
    "ExecutionContext",
    "NodeBase",
    "SubGraphNode",
]
