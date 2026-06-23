# SPDX-License-Identifier: Apache-2.0
"""sql-analyst run state — a natural-language question over structured data in,
a validated query result out.

The field names are the FR-23 declared output channels the engine
``SubGraphNode`` boundary translator enforces: a ``sql-analyst`` subgraph may
write only ``query`` / ``rows`` / ``answer`` / ``error`` / ``attempts`` /
``succeeded`` back into the parent state.

(The schema input is ``table_schema``, not ``schema`` — the latter shadows
pydantic's ``BaseModel.schema()`` and emits a runtime warning.)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SqlAnalystState(BaseModel):
    # Inputs
    question: str = ""
    table_schema: str = ""  # table DDL / description the generator sees
    max_attempts: int = 3

    # Outputs (declared channels)
    query: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    answer: str = ""
    error: str = ""
    attempts: int = 0
    succeeded: bool = False
