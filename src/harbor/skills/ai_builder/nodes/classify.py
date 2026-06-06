# SPDX-License-Identifier: Apache-2.0
"""Classifier — lightweight LLM route classifier for the AI Builder router.

Phase A: hardcodes `route = "basic"` (confidence 1.0). The function signature
is LLM-ready: `_call_model` accepts an optional model handle so the real
implementation can be swapped in once the model registry decision lands
(design §8 Q4).

The classifier asserts `(route-classified (value "<route>"))` into the Fathom
engine via the return dict. The Fathom/CLIPS rules in graph.yaml pattern-match
on this fact to dispatch to the correct destination node (design §2.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harbor.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

    from harbor.skills.ai_builder.state import Route


_ROUTE_KEYWORDS: dict[Route, list[str]] = {
    "shipwright": ["build", "create", "author", "design", "new graph", "write a graph"],
    "inspector": ["my graph", "my run", "my data", "show me", "list my"],
    "docs": ["how does", "what is", "explain", "documentation", "harbor docs"],
}


def _call_model(
    turn: str,
    history: list[dict[str, Any]],
    model: Any | None = None,
) -> tuple[Route, float, str]:
    """Classify `turn` into a route.

    Phase A: keyword heuristic, no LLM call. Returns (route, confidence, reasoning).
    The `model` parameter is reserved for Phase A polish / Phase B upgrade.
    """
    del model  # unused until model registry decision lands
    del history  # unused in heuristic; present for LLM-ready signature

    lower = turn.lower()
    for route, keywords in _ROUTE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return route, 0.8, f"keyword match: {kw!r}"

    # Default: basic chat (confidence 1.0 per design §3.1)
    return "basic", 1.0, "no specialist keyword matched; defaulting to basic"


class Classifier(NodeBase):
    """Route classifier node. Phase A: heuristic; Phase A polish: LLM-backed."""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        turn: str = getattr(state, "turn", "")
        history: list[Any] = list(getattr(state, "history", []))

        route, confidence, reasoning = _call_model(
            turn,
            [h.model_dump() if hasattr(h, "model_dump") else h for h in history],
        )

        return {
            "route": route,
            "route_confidence": confidence,
            "route_reasoning": reasoning,
        }
