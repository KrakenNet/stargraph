# SPDX-License-Identifier: Apache-2.0
"""JoinExit — assembles the response payload and emits an InterruptAction.

After any of the four route nodes (basic_chat, shipwright_gate, inspector_chat,
docs_chat) complete, this node fires. It is a pure assembly step: it reads
`response`, `route`, and `citations` from state, then returns them unchanged
so downstream CLIPS rules and UI projections have a clean, single source of
truth for the assembled turn.

The `InterruptAction` (stargraph/ir/_models.py:119, design §2.4 §4 §5.2) is
emitted by the `await_next_turn` node declared in graph.yaml (kind:
stargraph.nodes.interrupt). JoinExit itself is a pure Python node; it returns
the interrupt payload fields so the engine can propagate them when
`await_next_turn` fires `WaitingForInputEvent`.

Resumption: `POST /v1/runs/{id}/respond` injects the new `turn` into state;
the r-await-to-receive rule (graph.yaml) routes back to `receive_turn`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class JoinExit(NodeBase):
    """Pure join node — passes response, route, and citations through."""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        response: str = getattr(state, "response", "")
        route: str | None = getattr(state, "route", None)
        citations: list[Any] = list(getattr(state, "citations", []))

        # Return fields unchanged; await_next_turn node (stargraph.nodes.interrupt)
        # fires InterruptAction which emits WaitingForInputEvent (design §5.2).
        return {
            "response": response,
            "route": route,
            "citations": citations,
        }
