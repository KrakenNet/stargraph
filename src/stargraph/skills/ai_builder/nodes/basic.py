# SPDX-License-Identifier: Apache-2.0
"""BasicChat — plain LLM fallback for the basic route.

Reads `turn` and `history` from state, calls the LLM (or returns an explicit
"LLM not configured" message when no dspy.LM is wired), and writes
the result to `response` (design §3.1).

State contract: reads `turn`, `history`; writes `response`.
No citations, no `child_run_id` (design §3.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


_SYSTEM_PROMPT = (
    "You are the AI Builder assistant for StarGraph, a Stargraph workflow designer. "
    "You help users understand Stargraph concepts, debug graphs, and think through "
    "workflow designs. You have access to the conversation history below. "
    "When the user's question is specifically about their own graphs or data, "
    "say you can look that up if they'd like (Inspector mode). When asked about "
    "Stargraph framework internals, say you can search the docs. "
    "Keep responses concise and specific to Stargraph/StarGraph."
)

_LLM_NOT_CONFIGURED = (
    "LLM not configured. Set STARGRAPH_LLM_URL and STARGRAPH_LLM_MODEL "
    "environment variables (or pass --lm-url / --lm-model to stargraph serve) "
    "to enable AI Builder chat."
)


def _call_llm(
    turn: str,
    history: list[dict[str, Any]],
) -> str:
    """Call the LLM via dspy.Predict when dspy.settings.lm is set.

    Falls back to an explicit "LLM not configured" message (not a stub, not
    a fictional reply) when no LM is wired, per design §14.2 decision.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    if dspy.settings.lm is None:  # pyright: ignore[reportUnknownMemberType]
        return _LLM_NOT_CONFIGURED

    class _ChatSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
        """Chat with an AI Builder assistant."""

        system_prompt: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
        history: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
        user_turn: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
        response: str = dspy.OutputField()  # pyright: ignore[reportUnknownMemberType]

    predictor = dspy.Predict(_ChatSignature)  # pyright: ignore[reportUnknownMemberType]
    history_text = "\n".join(
        f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in history
    )
    result = predictor(  # pyright: ignore[reportUnknownMemberType]
        system_prompt=_SYSTEM_PROMPT,
        history=history_text,
        user_turn=turn,
    )
    return str(result.response)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


class BasicChat(NodeBase):
    """Plain LLM chat node. Calls dspy.Predict when an LM is configured."""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        turn: str = getattr(state, "turn", "")
        history: list[Any] = list(getattr(state, "history", []))

        response = _call_llm(
            turn,
            [h.model_dump() if hasattr(h, "model_dump") else h for h in history],
        )

        return {"response": response, "citations": []}
