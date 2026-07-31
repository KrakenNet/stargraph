# SPDX-License-Identifier: Apache-2.0
"""hitl-approval bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["ApprovalState"]


class ApprovalState(BaseModel):
    """propose -> interrupt -> apply state (benchmark T9; sequential)."""

    request: str = ""
    answer: str = ""
    rationale: str = ""
    approved_plan: str = ""
