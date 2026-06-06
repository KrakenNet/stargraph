# SPDX-License-Identifier: Apache-2.0
"""Shipwright run state — typed brief, interview, verifier results.

Annotated fields (`Mirror`) are projected to CLIPS at node boundaries
so the gap-detection and edit-routing packs can route on them.
"""

from __future__ import annotations

import warnings
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stargraph.ir import Mirror

ArtifactKind = Literal["graph", "pack"]
Mode = Literal["new", "fix"]
SlotOrigin = Literal["user", "rule", "llm", "loaded"]
QuestionKind = Literal["required", "edge_case", "reuse", "soft"]
QuestionOrigin = Literal["rule", "llm"]
VerifierKind = Literal["static", "tests", "smoke", "security", "perf", "architecture"]


class SpecSlot(BaseModel):
    name: str
    value: Any
    origin: SlotOrigin
    # Integer percent (0-100). FR-4 forbids floats in the structural hash
    # payload (model_json_schema includes the default), so ``confidence`` is
    # an int. ``100`` = full confidence; lower values flag rule/LLM-derived
    # slots so downstream nodes can prefer human-supplied answers.
    confidence: int = 100


with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message='Field name "schema" .* shadows an attribute in parent',
        category=UserWarning,
    )

    class Question(BaseModel):
        model_config = ConfigDict(protected_namespaces=())

        slot: str
        prompt: str
        kind: QuestionKind
        schema: dict[str, Any]  # pyright: ignore[reportIncompatibleMethodOverride]
        origin: QuestionOrigin


class VerifierResult(BaseModel):
    kind: VerifierKind
    passed: bool
    findings: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    duration_ms: int = 0


class State(BaseModel):
    # Routing
    mode: Annotated[Mode, Mirror()] = "new"
    kind: Annotated[ArtifactKind | None, Mirror()] = None

    # Inputs
    brief: str | None = None
    target_path: str | None = None

    # Context (--fix path)
    blast_radius: Annotated[list[str], Mirror()] = Field(default_factory=list)

    # Spec accumulation
    slots: Annotated[dict[str, SpecSlot], Mirror()] = Field(default_factory=dict)
    open_questions: Annotated[list[Question], Mirror()] = Field(default_factory=list[Question])
    answers: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])

    # Synthesis
    artifact_files: dict[str, str] = Field(default_factory=dict)
    locked_tests: Annotated[list[str], Mirror()] = Field(default_factory=list)

    # Verification
    verifier_results: Annotated[list[VerifierResult], Mirror()] = Field(
        default_factory=list[VerifierResult]
    )
    fix_attempts: Annotated[int, Mirror()] = 0

    # Output
    landing_summary: str | None = None
