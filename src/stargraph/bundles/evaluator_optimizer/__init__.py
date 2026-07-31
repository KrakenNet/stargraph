# SPDX-License-Identifier: Apache-2.0
"""evaluator-optimizer bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from stargraph.ir import Mirror

__all__ = ["RefineState"]


class RefineState(BaseModel):
    """generate -> judge -> refine loop state (benchmark T11)."""

    task: str = ""
    brief: str = ""
    answer: str = ""
    verdict: Annotated[str, Mirror(lifecycle="step")] = ""
    score: str = ""
    rationale: str = ""
