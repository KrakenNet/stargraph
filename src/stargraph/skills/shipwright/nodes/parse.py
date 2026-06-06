# SPDX-License-Identifier: Apache-2.0
"""ParseBrief — DSPy node that turns a freeform brief into typed slots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.shipwright.state import SpecSlot

if TYPE_CHECKING:
    from pydantic import BaseModel


class _BriefSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Extract the artifact kind, purpose, and any explicit node hints from a brief."""

    brief: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    kind: str = dspy.OutputField(desc="'graph' or 'pack'")  # pyright: ignore[reportUnknownMemberType]
    purpose: str = dspy.OutputField(desc="one-sentence purpose")  # pyright: ignore[reportUnknownMemberType]
    node_hints: list[str] = dspy.OutputField(desc="node names mentioned, possibly empty")  # pyright: ignore[reportUnknownMemberType]


class ParseBrief(NodeBase):
    """LLM-driven brief parser. `must_stub: true` in topology — replay-deterministic."""

    def __init__(self) -> None:
        self._predictor = dspy.Predict(_BriefSignature)  # pyright: ignore[reportUnknownMemberType]

    def _call_predictor(self, brief: str) -> dict[str, Any]:
        result = self._predictor(brief=brief)  # pyright: ignore[reportUnknownMemberType]
        return {
            "kind": result.kind,  # type: ignore[attr-defined]
            "purpose": result.purpose,  # type: ignore[attr-defined]
            "node_hints": result.node_hints,  # type: ignore[attr-defined]
        }

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = cast("str | None", getattr(state, "brief", None))
        if not brief:
            return {"slots": {}}
        parsed = self._call_predictor(brief)
        slots = {
            name: SpecSlot(name=name, value=value, origin="llm", confidence=70)
            for name, value in parsed.items()
        }
        return {"slots": slots}
