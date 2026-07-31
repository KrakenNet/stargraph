# SPDX-License-Identifier: Apache-2.0
"""State model for the ``std-tool-graph.yaml`` fixture (kind: tool e2e)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CalcState(BaseModel):
    """An expression in, the calculator tool's output dict out."""

    expression: str = "2 + 3 * 4"
    tool_result: dict[str, Any] = Field(default_factory=dict)
