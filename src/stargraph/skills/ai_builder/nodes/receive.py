# SPDX-License-Identifier: Apache-2.0
"""ReceiveTurn — ingests the current user turn into conversation history.

Pulls `turn` from state, appends it as a `ConversationTurn(role="user")`
to `history`, and returns both updated fields. This is the entry node of
every iteration of the HITL loop (design §4, graph.yaml r-await-to-receive).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.ai_builder.state import ConversationTurn

if TYPE_CHECKING:
    from pydantic import BaseModel


class ReceiveTurn(NodeBase):
    """Append the current turn to history and propagate state."""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        turn: str = getattr(state, "turn", "")
        history: list[ConversationTurn] = list(getattr(state, "history", []))

        if turn:
            history.append(ConversationTurn(role="user", text=turn))

        return {"turn": turn, "history": history}
