# SPDX-License-Identifier: Apache-2.0
"""deep-research bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from stargraph.ir import Mirror

__all__ = ["ResearchState"]


class ResearchState(BaseModel):
    """research -> completeness-judge loop state."""

    question: str = ""
    brief: str = ""
    answer: str = ""
    tool_trace: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    verdict: Annotated[str, Mirror(lifecycle="step")] = ""
    score: str = ""
    rationale: str = ""
