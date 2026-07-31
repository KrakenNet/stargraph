# SPDX-License-Identifier: Apache-2.0
"""Fixture nodes for the authoring-format loop test -- paired with
``test_authoring_cli.py``.

Mirrors ``cyclic_nodes.py`` but emits the authoring layer's standard
``verdict`` field (value routes branch on ``verdict`` only), so an
authored ``routes: {work: {refine: work, sufficient: finish}}`` doc can
drive the same two-round loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext


class WorkNode(NodeBase):
    """Increment ``rounds``; verdict stays ``refine`` until round 2."""

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
            "verdict": "sufficient" if rounds >= 2 else "refine",
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
