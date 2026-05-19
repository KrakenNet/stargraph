# SPDX-License-Identifier: Apache-2.0
"""Engine-side capability profile for the CVE remediation graph.

Production runs of ``harbor serve`` against ``cve-rem-pipeline`` MUST
construct a non-``None`` :class:`harbor.security.Capabilities` instance
or the engine-side gate is a silent no-op (see CRITERIA fancy #13). This
module wires the canonical, default-deny profile by:

* enumerating every ``@tool``-decorated callable referenced by the
  graph (servicenow.create_change_request, cargonet.cargonet_exec,
  cargonet.cargonet_list_nodes, cargonet.cargonet_find_node);
* deriving each tool's required permission strings from its
  :class:`harbor.ir.ToolSpec.permissions`;
* building a ``Capabilities(default_deny=True)`` whose ``granted`` set
  exactly covers those permissions and nothing else.

Result: a graph that imports a tool whose permission is NOT in this
allowlist will be denied at the engine boundary; the operator sees a
``CapabilityError`` instead of a silent execution.

Usage from a custom ``harbor serve`` lifespan::

    from demos.cve_remediation.capabilities import build_cve_rem_capabilities

    deps = {
        "scheduler": scheduler,
        "runs": {},
        "capabilities": build_cve_rem_capabilities(),
        ...
    }
    app = create_app(profile, deps=deps, lifespan=_lifespan)

The function is pure: no I/O, no env reads, deterministic. Calling it
twice yields equivalent ``Capabilities`` objects (``frozen=True``,
hashable claim set).
"""

from __future__ import annotations

from harbor.security import Capabilities, CapabilityClaim
from harbor.tools.cargonet import (
    cargonet_exec,
    cargonet_find_node,
    cargonet_list_nodes,
)
from harbor.tools.servicenow.create_change_request import (
    create_change_request,
)


def _claims_for_permission(permission: str) -> CapabilityClaim:
    """Return the minimal cleared-mode claim that grants ``permission``.

    Permission strings parse as ``<name>[:<scope>]``. Cleared deployments
    require a *scoped* claim, so ``tools:servicenow:write`` becomes
    ``CapabilityClaim(name="tools", scope="servicenow:write")``.
    """
    name, _sep, scope = permission.partition(":")
    return CapabilityClaim(name=name, scope=scope or None)


def build_cve_rem_capabilities() -> Capabilities:
    """Return the engine-side default-deny capability profile for cve-rem."""
    granted: set[CapabilityClaim] = set()
    for tool in (
        create_change_request,
        cargonet_exec,
        cargonet_find_node,
        cargonet_list_nodes,
    ):
        for perm in tool.spec.permissions:
            granted.add(_claims_for_permission(perm))
    return Capabilities(default_deny=True, granted=granted)
