# SPDX-License-Identifier: Apache-2.0
"""AI Builder router run state — conversation history, classifier output, response.

Annotated fields (`Mirror`) are projected to CLIPS at node boundaries so the
router's rule packs can dispatch on `route` and `route_confidence`.

Design §2.1 state schema.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from stargraph.ir import Mirror

Route = Literal["basic", "shipwright", "inspector", "docs"]


class ConversationTurn(BaseModel):
    """A single turn in the conversation history."""

    role: Literal["user", "assistant"]
    text: str
    route: Route | None = None


class State(BaseModel):
    # Inputs (injected at run start or on each respond call)
    turn: str = ""  # current user message
    history: list[ConversationTurn] = Field(default_factory=list[ConversationTurn])
    tenant_id: str = ""
    user_id: str = ""
    conversation_id: str = ""

    # Classifier output (§2.1)
    route: Annotated[Route | None, Mirror()] = None
    route_confidence: Annotated[float, Mirror()] = 0.0
    route_reasoning: str = ""

    # Response assembly
    response: str = ""
    citations: list[dict[str, object]] = Field(default_factory=list[dict[str, object]])

    # Shipwright child run (Option B, §2.5)
    child_run_id: str | None = None
    graph_id: str | None = None
