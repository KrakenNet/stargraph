# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #13: capability filter denies under-permissioned graph.

A graph that does NOT declare ``tools:servicenow:write`` must NOT be
able to resolve / invoke ``servicenow.create_change_request``. The
old POC stub returned ALL tools regardless of capabilities; the
default-deny gate (:class:`harbor.security.Capabilities`) closes that
hole.

Four scenarios -- against the *real* tool spec
(:func:`harbor.tools.servicenow.create_change_request`):

* **A. Cleared deployment, no claim** -- ``Capabilities(default_deny=True)``
  with empty ``granted`` set. ``caps.check(spec)`` must return False;
  loop-level gate would raise ``CapabilityError``.

* **B. Cleared deployment, exact-scoped claim** --
  ``CapabilityClaim(name="tools", scope="servicenow:write")``.
  ``caps.check(spec)`` must return True.

* **C. Cleared deployment, unscoped grant** --
  ``CapabilityClaim(name="tools")`` with ``default_deny=True``.
  Cleared mode refuses unscoped grants outright; ``check`` must return
  False.

* **D. POC mode (``default_deny=False``) + unscoped grant** -- the
  same unscoped claim now passes (dev-mode latitude). Demonstrates
  that today's stub-equivalent ("pass everything") is reachable only
  via explicit POC opt-in, never by accident in cleared deployments.

We also drive ``_check_node_capability``-shaped raise emulation by
constructing a default-deny capabilities and asserting that
``Capabilities.has_permission(required)`` returns False -- the same
predicate the loop uses to decide whether to raise
``CapabilityError``.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.verify_F13_capability_filter
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from harbor.errors import CapabilityError
from harbor.security import Capabilities, CapabilityClaim
from harbor.tools.cargonet import cargonet_exec
from harbor.tools.servicenow.create_change_request import (
    create_change_request,
)

if TYPE_CHECKING:
    from harbor.ir import ToolSpec


def _grade(label: str, got: bool, expect: bool) -> bool:
    icon = "OK" if got is expect else "FAIL"
    print(f"  [{label:5}] check={got!r:5}  expect={expect!r:5}  -> {icon}")
    return got is expect


def _gate_or_raise(caps: Capabilities, spec: "ToolSpec") -> bool:
    """Mirror the loop's deny path: raise CapabilityError on deny.

    Returns True on pass; on deny, raises CapabilityError so the
    verifier can confirm the loop-level behavior end-to-end.
    """
    if caps.check(spec):
        return True
    tool_id = f"{spec.namespace}.{spec.name}@{spec.version}"
    raise CapabilityError(
        f"capability denied for tool {tool_id!r}: required "
        f"{list(spec.permissions)!r}",
        capability=",".join(spec.permissions),
        tool_id=tool_id,
        deployment="loop",
    )


def main() -> int:
    overall = True
    print("=== F13 VERIFICATION (capability filter denies under-perm graph) ===\n")

    sn_spec = create_change_request.spec
    cn_spec = cargonet_exec.spec
    print(f"  servicenow.create_change_request permissions: "
          f"{sn_spec.permissions}")
    print(f"  cargonet.cargonet_exec           permissions: "
          f"{cn_spec.permissions}\n")

    # A. Cleared deployment, no claim -> must deny.
    print("--- A. Cleared (default_deny=True), no claim ---")
    caps_a = Capabilities(default_deny=True)
    if not _grade("check", caps_a.check(sn_spec), False):
        overall = False
    raised = False
    try:
        _gate_or_raise(caps_a, sn_spec)
    except CapabilityError as exc:
        raised = True
        print(f"  raise: CapabilityError -> {exc}")
    if not raised:
        print("  ! cleared default-deny gate did NOT raise on missing claim")
        overall = False

    # B. Cleared, exact-scoped claim -> pass.
    print("\n--- B. Cleared, exact-scoped claim "
          "(name='tools', scope='servicenow:write') ---")
    caps_b = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="tools", scope="servicenow:write")},
    )
    if not _grade("check", caps_b.check(sn_spec), True):
        overall = False

    # C. Cleared, unscoped grant -> deny (cleared rejects unscoped).
    print("\n--- C. Cleared, unscoped grant (refused under default_deny) ---")
    caps_c = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="tools")},
    )
    if not _grade("check", caps_c.check(sn_spec), False):
        overall = False

    # D. POC default_deny=False + unscoped grant -> pass.
    print("\n--- D. POC dev (default_deny=False) + unscoped grant ---")
    caps_d = Capabilities(
        default_deny=False,
        granted={CapabilityClaim(name="tools")},
    )
    if not _grade("check", caps_d.check(sn_spec), True):
        overall = False

    # E. Cross-tool: cargonet_exec carries its own permission set;
    #    a graph with only servicenow:write must still be denied for
    #    cargonet (proves capabilities are not all-or-nothing).
    print("\n--- E. Cross-tool: SN-only claim does NOT cover cargonet ---")
    if cn_spec.permissions:
        caps_e = Capabilities(
            default_deny=True,
            granted={CapabilityClaim(name="tools", scope="servicenow:write")},
        )
        if not _grade("check", caps_e.check(cn_spec), False):
            overall = False
    else:
        print("  cargonet_exec has no required permissions; skip.")

    # F'. Production capability profile factory: build the canonical
    #     cve-rem profile and check it covers every tool the graph
    #     declares (default-deny + scoped allowlist). This is the
    #     concrete answer to F13-3: cleared deployments wire this.
    print("\n--- F'. Production cve-rem capability factory ---")
    from demos.cve_remediation.capabilities import build_cve_rem_capabilities
    caps_prod = build_cve_rem_capabilities()
    print(f"  default_deny : {caps_prod.default_deny}")
    print(f"  granted size : {len(caps_prod.granted)}")
    if not caps_prod.default_deny:
        print("  ! production profile must be default_deny=True")
        overall = False
    if not _grade("prod-sn ", caps_prod.check(sn_spec), True):
        overall = False
    if not _grade("prod-cn ", caps_prod.check(cn_spec), True):
        overall = False

    # F. POC default: ``run.capabilities = None`` bypasses the gate
    #    silently (loop's ``_check_tool_capabilities`` early-returns).
    #    This is the *current production risk* — any deployment that
    #    forgets to wire a Capabilities instance into GraphRun gets
    #    no enforcement at all. Verifier reproduces this path via the
    #    same predicate the loop uses, so the risk is visible.
    print("\n--- F. POC default: capabilities=None silently bypasses gate ---")
    # Reproduce loop logic: if run.capabilities is None, _check_*_capability
    # early-returns. Demonstrate that the predicate path is the only
    # enforcement; the wiring (constructing Capabilities at all) is the
    # operator's responsibility.
    bypassed = True  # by definition: caps=None -> gate not consulted
    print(f"  [None ] caps=None -> gate skipped silently "
          f"(bypassed={bypassed})")
    print(
        "  ! WARNING: production deployments MUST wire Capabilities; verify "
        "your harbor lifecycle configures default_deny=True. This verifier "
        "asserts the predicate works, NOT that runtime constructs use it."
    )
    # We do NOT mark this as a test failure -- the bypass is by design
    # for POC mode -- but we record the warning in a way the operator
    # cannot miss. (Tracked separately as a hardening task.)

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
