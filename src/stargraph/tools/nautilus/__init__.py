# SPDX-License-Identifier: Apache-2.0
"""stargraph.tools.nautilus -- :func:`broker_request` registry-discoverable tool (FR-45, §8.2).

Re-exports :func:`broker_request` so consumers can reach it via either
the module path (``stargraph.tools.nautilus.broker_request:broker_request``)
or the package short form (``stargraph.tools.nautilus:broker_request``).
"""

from __future__ import annotations

from stargraph.tools.nautilus.broker_request import broker_request

__all__ = ["broker_request"]
