# SPDX-License-Identifier: Apache-2.0
"""everything-demo custom node factories.

The IR's `module:ClassName` escape hatch (`stargraph.cli.run._resolve_node_factory`)
imports these. Every class is a `NodeBase` subclass with a zero-arg
constructor — that is the contract `_resolve_node_factory` enforces.

Two patterns covered here:

1. **Domain-named wrappers around built-in nodes** (start sentinel,
   branch_response). These keep the IR readable while staying inside
   the supported plugin API.
2. **Custom domain nodes** (lookup_history caller). Demonstrates how a
   graph author drops an arbitrary `NodeBase` into the IR without
   touching Stargraph core.

For real plugin distribution, declare entry points under
``stargraph.tools`` / ``stargraph.skills`` / ``stargraph.stores`` /
``stargraph.triggers``. There is no ``stargraph.nodes`` group yet (see
TODO.md "Add `stargraph.nodes` entry-point group" item) — until then,
custom nodes are imported via the ``module:ClassName`` IR ``kind:``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import EchoNode, ExecutionContext, NodeBase
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel


class StartSentinel(EchoNode):
    """Marker node at the head of the graph. Pure passthrough."""


class BranchResponse(NodeBase):
    """Pattern-match the HITL response decision into a routing flag.

    Reads ``state.response.decision`` and writes
    ``state.validation_passed`` so downstream rules can pattern-match
    on a primitive instead of an embedded model. Replay-safe.
    """

    SIDE_EFFECTS = SideEffects.read
    REPLAY_POLICY = ReplayPolicy.recorded_result

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        response = getattr(state, "response", None)
        if response is None:
            return {"validation_passed": False}
        decision = getattr(response, "decision", None)
        return {"validation_passed": decision == "approve"}


class LookupHistoryCaller(NodeBase):
    """Wraps the ``demo.lookup_history`` ``@tool`` for graph dispatch.

    Demonstrates calling a registered tool from a ``NodeBase`` subclass.
    Real graphs would resolve the tool via the engine's ToolRegistry;
    this POC imports the callable directly so the demo runs without a
    plugin-load cycle.
    """

    SIDE_EFFECTS = SideEffects.read
    REPLAY_POLICY = ReplayPolicy.recorded_result

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        from demos.everything_demo.graph.tools import lookup_history

        result = await lookup_history(ticket_id=getattr(state, "ticket_id", ""))
        return {
            "history_count": int(result.get("count", 0)),
            "history_summary": str(result.get("summary", "")),
        }


__all__ = ["BranchResponse", "LookupHistoryCaller", "StartSentinel"]
