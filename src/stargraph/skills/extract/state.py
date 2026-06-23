# SPDX-License-Identifier: Apache-2.0
"""Extract run state — unstructured text in, validated fields out.

The field names are the FR-23 declared output channels the engine
``SubGraphNode`` boundary translator enforces: an ``extract`` subgraph may
write only ``fields`` / ``missing`` / ``valid`` back into the parent state.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractState(BaseModel):
    # Inputs
    text: str = ""
    target_fields: list[str] = Field(default_factory=list[str])

    # Outputs (declared channels)
    fields: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list[str])
    valid: bool = False
