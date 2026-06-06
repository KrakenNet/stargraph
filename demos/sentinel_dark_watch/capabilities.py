# SPDX-License-Identifier: Apache-2.0
"""Engine-side capability profile for the Sentinel Dark Watch graph.

Default-deny profile that grants:

* every ``@tool``-decorated callable referenced by the SDW graph
  (``nautilus.broker_request``);
* the ``runs:respond`` capability required by the HITL analyst-review
  gate (AnalystReviewNode raises ``_HitInterrupt``; the Streamlit UI
  calls ``POST /v1/runs/{id}/respond`` to resume).

Usage from ``serve_sdw.py``::

    from demos.sentinel_dark_watch.capabilities import build_sdw_capabilities

    deps = {
        "capabilities": build_sdw_capabilities(),
        ...
    }
"""

from __future__ import annotations

from stargraph.security import Capabilities, CapabilityClaim
from stargraph.tools.nautilus import broker_request


def _claims_for_permission(permission: str) -> CapabilityClaim:
    """Return the minimal cleared-mode claim that grants ``permission``."""
    name, _sep, scope = permission.partition(":")
    return CapabilityClaim(name=name, scope=scope or None)


def build_sdw_capabilities() -> Capabilities:
    """Return the engine-side default-deny capability profile for SDW."""
    granted: set[CapabilityClaim] = set()
    # Tool-derived permissions (nautilus.broker_request).
    for tool in (broker_request,):
        for perm in tool.spec.permissions:
            granted.add(_claims_for_permission(perm))
    # HITL respond capability for AnalystReviewNode.
    granted.add(CapabilityClaim(name="runs", scope="respond"))
    return Capabilities(default_deny=True, granted=granted)
