# SPDX-License-Identifier: Apache-2.0
"""RequestApproval — validate the proposed action and apply the policy gate.

The first node of the ``approval`` workflow. It rejects an empty action
loudly, then lets an optional ``auto_approve`` policy pre-approve low-risk
actions; everything else is left ``pending``, awaiting a human verdict that
:class:`stargraph.skills.approval.nodes.decide.ApplyDecision` supplies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class RequestApproval(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # no per-run context needed to open the gate
        action = str(getattr(state, "action", "") or "")
        if not action.strip():
            raise ValueError("action is required: nothing to approve")

        if bool(getattr(state, "auto_approve", False)):
            return {
                "status": "approved",
                "approved": True,
                "decided_by": "policy",
                "reason": "auto-approved by policy",
            }
        return {"status": "pending"}
