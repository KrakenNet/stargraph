# SPDX-License-Identifier: Apache-2.0
"""Cyclic rule-routing fixture nodes -- paired with ``cyclic-graph.yaml``.

The ``work`` node's Mirror-annotated ``phase_verdict`` stays ``"refine"``
until its second round, so the graph's rules must route work -> work ->
finish. A linear driver runs each node exactly once in declaration order,
so ``rounds == 2`` in the final state proves rules fired live.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel

from stargraph.ir import Mirror
from stargraph.nodes.base import ExecutionContext, NodeBase


class RouteState(BaseModel):
    """Two routed fields plus a message the finish node writes."""

    rounds: int = 0
    phase_verdict: Annotated[str, Mirror(lifecycle="step")] = "pending"
    message: str = ""


class WorkNode(NodeBase):
    """Increment ``rounds``; demand a second round before ``"sufficient"``."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        rounds_prev: object = getattr(state, "rounds")  # noqa: B009
        assert isinstance(rounds_prev, int)
        rounds = rounds_prev + 1
        return {
            "rounds": rounds,
            "phase_verdict": "sufficient" if rounds >= 2 else "refine",
        }


class FinishNode(NodeBase):
    """Record how many work rounds ran before the rules routed here."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        rounds: object = getattr(state, "rounds")  # noqa: B009
        return {"message": f"finished after {rounds} rounds"}
