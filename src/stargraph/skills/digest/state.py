# SPDX-License-Identifier: Apache-2.0
"""Digest run state — long text in, condensed summary out.

The field names are the FR-23 declared output channels the engine
``SubGraphNode`` boundary translator enforces: a ``digest`` subgraph may write
only ``chunks`` / ``partials`` / ``summary`` back into the parent state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DigestState(BaseModel):
    # Inputs
    text: str = ""
    chunk_size: int = 2000

    # Outputs (declared channels)
    chunks: list[str] = Field(default_factory=list[str])
    partials: list[str] = Field(default_factory=list[str])
    summary: str = ""
