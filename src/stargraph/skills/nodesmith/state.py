# SPDX-License-Identifier: Apache-2.0
"""Nodesmith run state — spec in, generated node + verifier results out.

The graph runs linearly (triage → recall → build → record); the bounded repair
loop lives inside the ``build`` node, so no rule-routing fields are needed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VerifierResult(BaseModel):
    """One gate result. ``kind`` ∈ {static, contract, tests}."""

    kind: str
    passed: bool
    findings: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    duration_ms: int = 0


class State(BaseModel):
    # Inputs (seeded via `--inputs key=value`)
    brief: str | None = None
    node_name: str | None = None
    model_id: str = ""
    output_dir: str = ""

    # Reflexion context (recalled before the build)
    recalled_lessons: list[str] = Field(default_factory=list[str])

    # Build outputs (final attempt)
    class_name: str = ""
    reads: list[str] = Field(default_factory=list[str])
    writes: list[str] = Field(default_factory=list[str])
    fixture: dict[str, Any] = Field(default_factory=dict)
    artifact_files: dict[str, str] = Field(default_factory=dict)

    # Verification outcome
    verifier_results: list[VerifierResult] = Field(default_factory=list[VerifierResult])
    fix_attempts: int = 0
    succeeded: bool = False

    # Output
    landed_path: str = ""
