# SPDX-License-Identifier: Apache-2.0
"""Sentinel Dark Watch — graph node implementations.

Each node subclasses :class:`harbor.nodes.base.NodeBase` and returns a
dict of state-field mutations merged by the execution loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harbor.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class PassthroughNode(NodeBase):
    """No-op node used for branch_resp_review and action_done."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        return {}
