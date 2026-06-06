# SPDX-License-Identifier: Apache-2.0
"""stargraph.security -- capability-based access control (NFR-7, design §3.11).

Phase 1 POC ships :class:`stargraph.security.capabilities.Capabilities` --
an immutable, default-deny gate that the tool-execution path
(:mod:`stargraph.runtime.tool_exec`, task 1.27) consults before invoking
any side-effectful tool. :class:`CapabilityClaim` is the namespace +
glob-scope token granted to a deployment; :meth:`Capabilities.check`
matches a :class:`stargraph.ir.ToolSpec`'s ``permissions`` list against the
granted set.

Threat-model boundary (design §3.11): MCP servers and third-party
plugins are untrusted; every ``call_tool`` is gated by a
:class:`Capabilities` instance threaded through ``Graph.start``. This
module exposes the type only -- enforcement wiring lands at task 1.27.
"""

from __future__ import annotations

from stargraph.security.capabilities import Capabilities, CapabilityClaim

__all__ = [
    "Capabilities",
    "CapabilityClaim",
]
