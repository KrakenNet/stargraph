# SPDX-License-Identifier: Apache-2.0
"""Approval run state — a proposed action in, a human/policy verdict out.

The field names are the FR-23 declared output channels the engine
``SubGraphNode`` boundary translator enforces: an ``approval`` subgraph may
write only ``status`` / ``approved`` / ``decided_by`` / ``reason`` (alongside
the inputs it reads) back into the parent state.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApprovalState(BaseModel):
    # Inputs
    action: str = ""  # what needs approval
    payload: dict[str, Any] = Field(default_factory=dict)
    auto_approve: bool = False  # policy that may pre-approve low-risk actions

    # Outputs (declared channels)
    status: str = "pending"  # "pending" / "approved" / "denied"
    approved: bool = False
    decided_by: str = ""
    reason: str = ""
