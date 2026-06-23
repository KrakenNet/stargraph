# SPDX-License-Identifier: Apache-2.0
"""ApplyDecision — resolve the gate with a policy pass-through or a human verdict.

The second node of the ``approval`` workflow. A policy pre-approval from
:class:`stargraph.skills.approval.nodes.request.RequestApproval` passes through
unchanged. Otherwise the human verdict arrives behind the injectable
``decider`` seam (standing in for the engine's HITL pause + ``/respond``), so
the node's value-add — applying a deny-by-default posture and recording who
decided and why — is exercised in tests with no live pause. The default seam
raises, because an unwired gate has no verdict to apply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.errors import StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    # state -> (approved, reason) the human supplied via the HITL seam.
    Decider = Callable[[BaseModel], tuple[bool, str]]


class ApplyDecision(NodeBase):
    def __init__(self, decider: Decider | None = None) -> None:
        self._decider = decider or _default_decider

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # the verdict arrives through the decider seam, not ctx
        # Policy already pre-approved upstream: pass through unchanged.
        if str(getattr(state, "status", "") or "") == "approved":
            return {}

        approved, reason = self._decider(state)
        status = "approved" if approved else "denied"
        return {
            "status": status,
            "approved": bool(approved),
            "decided_by": "human",
            "reason": reason,
        }


def _default_decider(state: BaseModel) -> tuple[bool, str]:
    """Production decider — no verdict without a wired human seam.

    The real path resolves through the engine's HITL pause and ``/respond``;
    absent that wiring there is no verdict to apply, so the gate loud-fails
    rather than silently approving or denying.
    """
    del state
    raise StargraphRuntimeError("no decider wired — approval requires a human verdict via /respond")
