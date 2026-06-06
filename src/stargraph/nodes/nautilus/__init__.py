# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.nautilus -- built-in :class:`BrokerNode` (FR-44, FR-46, design §8.1).

Re-exports :class:`BrokerNode` and :class:`BrokerNodeConfig` so graphs
can reference the node by either its module path
(``stargraph.nodes.nautilus.broker_node:BrokerNode``) or the package short
form (``stargraph.nodes.nautilus:BrokerNode``).
"""

from __future__ import annotations

from stargraph.nodes.nautilus.broker_node import BrokerNode, BrokerNodeConfig

__all__ = ["BrokerNode", "BrokerNodeConfig"]
