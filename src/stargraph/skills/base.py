# SPDX-License-Identifier: Apache-2.0
"""Skill base class -- Plugin API §3 surface (design §3.7).

Pydantic-typed manifest for a registerable skill (FR-21, FR-22, FR-23,
FR-24). The Plugin loader pre-validates a :class:`Skill` instance,
checks namespace conflicts, and registers it via the existing
``register_skills`` hookspec; engine ``SubGraphNode`` consumes
``state_schema`` field names as the declared-output write whitelist
(FR-23). ``bubble_events`` defaults to ``True`` (FR-24,
LangGraph #2484 mitigation).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, get_args, get_origin

from pydantic import BaseModel, Field, computed_field, model_validator

__all__ = [
    "Example",
    "Skill",
    "SkillKind",
]


class SkillKind(StrEnum):
    """Skill role taxonomy (design §3.7)."""

    agent = "agent"
    workflow = "workflow"
    utility = "utility"


class Example(BaseModel):
    """Few-shot example carried in :attr:`Skill.examples` (design §3.7)."""

    inputs: dict[str, Any]
    expected_output: dict[str, Any] | None = None


class Skill(BaseModel):
    """Plugin API §3 surface (FR-21, FR-22, FR-23, FR-24).

    Pydantic-typed and manifest-validated. Skills register via the
    existing ``register_skills`` hookspec; pluggy loader pre-validates
    each instance. ``state_schema`` declares the output channels —
    engine ``SubGraphNode`` rejects undeclared parent-state writes at
    boundary translation time (replay-first stance per design §3.7).
    """

    name: str  # slug; same validator as ToolSpec
    version: str  # SemVer
    kind: SkillKind
    description: str
    tools: list[str] = Field(default_factory=list)  # tool ids `<ns>.<name>@<ver>`
    subgraph: str | None = None  # path to IR document or inline IRDocument ref
    system_prompt: str | None = None
    state_schema: type[BaseModel]  # declared output channels live here
    requires: list[str] = Field(default_factory=list)  # capability strings
    examples: list[Example] = Field(default_factory=list[Example])
    bubble_events: bool = True  # FR-24 default-on (LangGraph #2484 mitigation)
    declared_output_keys: frozenset[str] = Field(default_factory=frozenset[str])

    @computed_field  # type: ignore[prop-decorator]
    @property
    def site_id(self) -> str:
        """Content-addressable subgraph site identifier (FR-23, AC-3.5).

        Deterministic and replay-safe: derived purely from manifest
        content, **NOT** assigned by call order. Two registrations of
        the same ``(name, version)`` always yield the same ``site_id``,
        which the engine ``SubGraphNode`` uses as a stable handle for
        checkpointing and replay (design §3.7).

        POC formula: ``f"{name}@{version}"``. Full implementation will
        derive from the skill's IR position (subgraph node coordinates)
        once ``SubGraphNode`` integration lands in Phase 3.
        """
        return f"{self.name}@{self.version}"

    @model_validator(mode="after")
    def _validate_declared_outputs(self) -> Skill:
        """FR-23 declared-output-channels-only check (design §3.7, AC-3.3).

        Walks ``state_schema`` field annotations, rejects any ``set`` /
        ``set[X]`` fields (NFR-2 inheritance: replay-safe state must use
        ``frozenset``), and exposes the surviving field names as the
        :attr:`declared_output_keys` write whitelist. Engine
        ``SubGraphNode`` (Phase 3) consumes this whitelist at boundary
        translation time so undeclared parent-state writes loud-fail at
        registration -- not at runtime.
        """
        for field_name, info in self.state_schema.model_fields.items():
            ann = info.annotation
            origin = get_origin(ann)
            if ann is set or origin is set:
                raise ValueError(
                    f"state_schema field '{field_name}' is typed as "
                    f"'set' (got {ann!r}); use 'frozenset' instead "
                    "(NFR-2: replay-safe state requires hashable, "
                    "immutable collections)."
                )
            # Defensive: catch nested set inside generics like list[set[X]]
            for arg in get_args(ann) or ():
                if arg is set or get_origin(arg) is set:
                    raise ValueError(
                        f"state_schema field '{field_name}' contains a "
                        f"nested 'set' annotation ({ann!r}); use "
                        "'frozenset' instead (NFR-2)."
                    )
        object.__setattr__(
            self,
            "declared_output_keys",
            frozenset(self.state_schema.model_fields.keys()),
        )
        return self
