# SPDX-License-Identifier: Apache-2.0
"""triage-router bundle state -- referenced by ``graph.yaml`` ``state_class``."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from stargraph.ir import Mirror

__all__ = ["TriageState"]


class TriageState(BaseModel):
    """classify -> per-label dispatch state (benchmark T4).

    The classifier writes its label into ``verdict`` (with
    ``confidence``); rules goto the label's handler. The LLM emits
    facts -- Fathom does the routing.
    """

    item: str = ""
    verdict: Annotated[str, Mirror(lifecycle="step")] = ""
    confidence: str = ""
    answer: str = ""
    summary: str = ""
    rationale: str = ""
