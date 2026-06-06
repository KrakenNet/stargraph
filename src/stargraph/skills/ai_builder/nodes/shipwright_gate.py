# SPDX-License-Identifier: Apache-2.0
"""ShipwrightGate — Phase B stub.

Full implementation (design §3.2, §9 Phase B):
  - Detect new vs continuation session (child_run_id in history).
  - New: POST /v1/runs {graph_id: "graph:shipwright"} to start a child run.
  - Continuation: POST /v1/runs/{child_run_id}/respond to relay user turn.
  - Store child_run_id in state for UI correlation.

Phase B blocker: Shipwright must support live HITL (InterruptAction in
human_input node). See design §8 Q1 and graph.yaml line 33 comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class ShipwrightGate(NodeBase):
    """Stub — routes to Shipwright graph authoring flow (Phase B)."""

    # TODO Phase B: implement new-session and continuation paths (design §3.2).
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        return {
            "response": "shipwright not yet implemented (Phase B)",
            "citations": [],
        }
