# SPDX-License-Identifier: Apache-2.0
"""The typed build manifest — the planner's output, the executor's input.

A foundry run decomposes a natural-language request into a :class:`BuildManifest`:
an ordered list of typed build items, exactly one of which is the runnable graph
spine (kind ``"graph"``) and the rest of which are capabilities mounted around it.
This schema is the contract between the LLM planner (which fills it) and the
deterministic executor (which runs each item's smith), so a malformed plan is
rejected here — before any smith runs.
"""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = ["KNOWN_KINDS", "SPINE_KIND", "BuildManifest", "ManifestItem", "coerce"]

# Each kind maps 1:1 to a smith (see :mod:`stargraph.skills.foundry.dispatch`).
KNOWN_KINDS = frozenset(
    {"graph", "node", "tool", "store", "trigger", "adapter", "ml", "pack", "skill", "plugin"}
)
SPINE_KIND = "graph"


class ManifestItem(BaseModel):
    """One artifact to build: which smith (``kind``), a slug (``name``), and the
    ``brief`` handed verbatim to that smith. ``config`` carries optional structured
    hints (unused by the skeleton; reserved for capability wiring)."""

    kind: str
    name: str
    brief: str
    config: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("kind")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in KNOWN_KINDS:
            raise ValueError(f"unknown build kind {v!r}; one of {sorted(KNOWN_KINDS)}")
        return v

    @field_validator("name", "brief")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("manifest item name and brief must be non-empty")
        return v


class BuildManifest(BaseModel):
    """A validated plan: one ``graph`` spine plus zero or more capabilities."""

    items: list[ManifestItem]

    @model_validator(mode="after")
    def _exactly_one_spine(self) -> BuildManifest:
        spines = [i for i in self.items if i.kind == SPINE_KIND]
        if len(spines) != 1:
            raise ValueError(f"a build needs exactly one {SPINE_KIND!r} spine, got {len(spines)}")
        return self

    @property
    def spine(self) -> ManifestItem:
        return next(i for i in self.items if i.kind == SPINE_KIND)

    @property
    def capabilities(self) -> list[ManifestItem]:
        return [i for i in self.items if i.kind != SPINE_KIND]


def coerce(raw: Any) -> BuildManifest:
    """Parse a planner's raw output into a validated manifest.

    Accepts a JSON string, a ``{"items": [...]}`` dict, or a bare list of items —
    the shapes an LLM realistically returns. Raises on malformed/invalid plans
    (``json.JSONDecodeError`` or pydantic ``ValidationError``)."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, list):
        raw = {"items": cast("list[Any]", raw)}
    return BuildManifest.model_validate(raw)
