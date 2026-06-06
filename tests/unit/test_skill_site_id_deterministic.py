# SPDX-License-Identifier: Apache-2.0
"""TDD-GREEN: FR-23 / AC-3.5 -- Skill ``site_id`` determinism.

The engine ``SubGraphNode`` keys checkpoint slots by
:attr:`Skill.site_id`, so the value must be a pure function of the
manifest -- never a call-order counter -- to keep replay safe (design
§3.7).

POC formula (asserted here): ``f"{name}@{version}"``. Once the
``SubGraphNode`` integration lands the full implementation derives from
IR position, but determinism on ``(name, version)`` remains the public
contract.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from stargraph.skills.base import Skill, SkillKind

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


class _State(BaseModel):
    """Minimal ``state_schema`` for Skill manifest validation."""

    answer: str = ""


def _make_skill(*, name: str = "rag", version: str = "0.1.0") -> Skill:
    """Build a minimal Skill manifest for site_id assertions."""
    return Skill(
        name=name,
        version=version,
        kind=SkillKind.agent,
        description="site_id determinism fixture",
        state_schema=_State,
    )


def test_same_name_version_same_site_id() -> None:
    """Two manifests with identical (name, version) collide on site_id."""
    a = _make_skill(name="rag", version="0.1.0")
    b = _make_skill(name="rag", version="0.1.0")

    assert a.site_id == b.site_id


def test_different_name_different_site_id() -> None:
    """Differing names produce distinct site_ids."""
    a = _make_skill(name="rag", version="0.1.0")
    b = _make_skill(name="search", version="0.1.0")

    assert a.site_id != b.site_id


def test_different_version_different_site_id() -> None:
    """Differing versions produce distinct site_ids."""
    a = _make_skill(name="rag", version="0.1.0")
    b = _make_skill(name="rag", version="0.2.0")

    assert a.site_id != b.site_id


def test_site_id_format_name_at_version() -> None:
    """POC contract: ``site_id == f"{name}@{version}"``."""
    skill = _make_skill(name="rag", version="0.1.0")

    assert skill.site_id == "rag@0.1.0"
