# SPDX-License-Identifier: Apache-2.0
"""orchestrator-workers bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["OrchestratorState"]


class OrchestratorState(BaseModel):
    """plan -> worker -> synthesize state (benchmark T5/T10; sequential v1)."""

    task: str = ""
    tasks: list[str] = Field(default_factory=list)
    brief: str = ""
    answer: str = ""
    tool_trace: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    synth_input: str = ""
    summary: str = ""
