# SPDX-License-Identifier: Apache-2.0
"""everything-demo tool definitions.

Two tools, one of each side-effect class:

- :func:`lookup_history` -- ``side_effects=read`` -> ``replay_policy=recorded_result``.
- :func:`notify_user` -- ``side_effects=external`` -> ``replay_policy=must_stub``.

Both use the :func:`stargraph.tools.tool` decorator; the resulting
callables expose ``.spec: ToolSpec`` and are registered via the
``register_tools`` hookspec when this module is loaded as a plugin
(see ``pyproject.toml`` entry-point declarations in the demo's
``README.md``).
"""

from __future__ import annotations

from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects


@tool(
    namespace="demo",
    name="lookup_history",
    version="1.0.0",
    side_effects=SideEffects.read,
)
async def lookup_history(*, ticket_id: str, limit: int = 5) -> dict[str, Any]:
    """Read prior tickets for ``ticket_id``'s submitter (POC stub).

    Real impl would hit the doc store; this stub returns a deterministic
    shape so smoke tests can exercise the wiring.
    """
    return {
        "count": 0,
        "summary": f"no prior tickets for {ticket_id}",
    }


@tool(
    namespace="demo",
    name="notify_user",
    version="1.0.0",
    side_effects=SideEffects.external,
    replay_policy=ReplayPolicy.must_stub,
)
async def notify_user(*, ticket_id: str, decision: str, note: str = "") -> dict[str, Any]:
    """External notify (Slack / email) — replay-stubbed by must_stub.

    Wire through the MCP adapter (``stargraph.adapters.mcp.bind``) when an
    MCP server provides the ``notify_user`` tool surface; otherwise this
    POC stub is the in-process path.
    """
    return {"status": "sent", "ticket_id": ticket_id, "decision": decision, "note": note}


__all__ = ["lookup_history", "notify_user"]
