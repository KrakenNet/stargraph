# SPDX-License-Identifier: Apache-2.0
"""Skill base class shape -- Plugin API §3 surface (FR-21, FR-22).

Asserts that :class:`stargraph.skills.base.Skill` exposes every field listed
in design §3.7's Plugin API §3 surface, that ``bubble_events`` defaults
to ``True`` (FR-24, LangGraph #2484 mitigation), and that ``kind`` only
accepts :class:`SkillKind` enum values.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from stargraph.skills.base import Skill, SkillKind

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


class _DemoState(BaseModel):
    """Trivial state schema with one declared output channel."""

    answer: str = ""


def _make_skill(**overrides: object) -> Skill:
    """Construct a minimal valid :class:`Skill` for shape assertions."""
    base: dict[str, object] = {
        "name": "demo",
        "version": "0.1.0",
        "kind": SkillKind.agent,
        "description": "Demo skill for shape tests.",
        "state_schema": _DemoState,
    }
    base.update(overrides)
    return Skill(**base)  # pyright: ignore[reportArgumentType]


def test_skill_has_all_plugin_api_fields() -> None:
    """Every Plugin API §3 field is present on a constructed Skill."""
    skill = _make_skill()
    expected_fields = {
        "name",
        "version",
        "kind",
        "description",
        "tools",
        "subgraph",
        "system_prompt",
        "state_schema",
        "requires",
        "examples",
        "bubble_events",
    }
    for field in expected_fields:
        assert hasattr(skill, field), f"Skill missing field {field!r}"


def test_bubble_events_default_true() -> None:
    """FR-24: ``bubble_events`` defaults to ``True``."""
    skill = _make_skill()
    assert skill.bubble_events is True


def test_kind_is_skillkind_enum() -> None:
    """``kind`` accepts only :class:`SkillKind` values; junk strings reject."""
    for member in SkillKind:
        skill = _make_skill(kind=member)
        assert skill.kind is member

    with pytest.raises(ValidationError):
        _make_skill(kind="not-a-real-kind")
