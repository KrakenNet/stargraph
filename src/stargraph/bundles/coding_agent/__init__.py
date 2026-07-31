# SPDX-License-Identifier: Apache-2.0
"""coding-agent bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from stargraph.ir import Mirror

__all__ = ["CodingState"]


class CodingState(BaseModel):
    """plan -> brief -> code -> review fix-loop state.

    ``verdict`` is the routed field: the ``code`` node writes pass/fail
    from the exit code, the ``review`` judge overwrites it, and the
    rules only match it on the ``review`` node-id.
    """

    task: str = ""
    tasks: list[str] = Field(default_factory=list)
    brief: str = ""
    code: str = ""
    run_result: dict[str, Any] = Field(default_factory=dict)
    review_input: str = ""
    verdict: Annotated[str, Mirror(lifecycle="step")] = ""
    score: str = ""
    rationale: str = ""
