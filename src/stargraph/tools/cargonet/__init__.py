# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.cargonet`` -- live exec into the CargoNet digital twin.

CargoNet is the simulated network running real Linux containers (Alpine
+ assorted package managers). The pipeline treats CargoNet nodes as
production targets: probes, applies, and verify-step assertions all
go through the CargoNet REST surface so we never have to hard-code
host inventories or shell out to ``docker exec``.

Tools registered here:

* :func:`cargonet_list_nodes` -- GET ``/api/v1/labs/{lab}/nodes``
* :func:`cargonet_exec`       -- POST ``/api/v1/labs/{lab}/nodes/{node}/exec``
* :func:`cargonet_find_node`  -- name-based lookup, returns ``(lab_id, node_id)``

All three are network calls; ``cargonet_exec`` mutates the target. The
write-tier surface has ``side_effects=external`` and the
``tools:cargonet:exec`` capability so a graph that doesn't declare the
capability cannot resolve it.
"""

from __future__ import annotations

from stargraph.tools.cargonet.exec_node import (
    cargonet_exec,
    cargonet_find_node,
    cargonet_list_nodes,
)

__all__ = [
    "cargonet_exec",
    "cargonet_find_node",
    "cargonet_list_nodes",
]
