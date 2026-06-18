# SPDX-License-Identifier: Apache-2.0
"""Triage run state — an incoming item in, a category + route decision out.

The field names are the FR-23 declared output channels the engine
``SubGraphNode`` boundary translator enforces: a ``triage`` subgraph may write
only ``category`` / ``route`` / ``priority`` / ``matched_rules`` back into the
parent state (alongside echoing its own inputs).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TriageState(BaseModel):
    # Inputs
    subject: str = ""
    body: str = ""
    signals: dict[str, Any] = Field(default_factory=dict)

    # Outputs (declared channels)
    category: str = ""
    route: str = ""
    priority: str = ""
    matched_rules: list[str] = Field(default_factory=list[str])
