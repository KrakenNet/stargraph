# SPDX-License-Identifier: Apache-2.0
"""rag-qa bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from stargraph.ir import Mirror

__all__ = ["RagQAState"]


class RagQAState(BaseModel):
    """retrieve -> answer -> groundedness-judge loop state (benchmark T6)."""

    question: str = ""
    brief: str = ""
    answer: str = ""
    citations: list[str] = Field(default_factory=list)
    review_input: str = ""
    verdict: Annotated[str, Mirror(lifecycle="step")] = ""
    score: str = ""
    rationale: str = ""
