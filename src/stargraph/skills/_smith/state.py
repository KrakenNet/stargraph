# SPDX-License-Identifier: Apache-2.0
"""SmithState — the generic run-state spine shared by every smith.

Holds the fields the shared lifecycle nodes (triage → recall → build → record)
read and write, none of which are domain-specific: the brief + run config, the
recalled grounding, the build outputs common to all smiths, and the landing
path. Each smith subclasses this and adds only its domain output fields (a node
adds ``class_name``/``reads``/``writes``; a tool adds ``tool_name``/``namespace``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from stargraph.skills._smith.gate import VerifierResult

__all__ = ["SmithState", "VerifierResult"]


class SmithState(BaseModel):
    # Inputs (seeded via `--inputs key=value`)
    brief: str | None = None
    model_id: str = ""
    output_dir: str = ""

    # Reflexion + RAG grounding (recalled before the build)
    recalled_lessons: list[str] = Field(default_factory=list[str])
    recalled_context: str = ""

    # Build outputs common to every smith
    artifact_files: dict[str, str] = Field(default_factory=dict)
    verifier_results: list[VerifierResult] = Field(default_factory=list[VerifierResult])
    fix_attempts: int = 0
    succeeded: bool = False

    # Output
    landed_path: str = ""
