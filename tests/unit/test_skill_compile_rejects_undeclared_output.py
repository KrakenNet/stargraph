# SPDX-License-Identifier: Apache-2.0
"""``Skill.declared_output_keys`` is the FR-23 write whitelist (design §3.7).

The engine ``SubGraphNode`` consumes ``declared_output_keys`` as the
boundary-translation write whitelist; undeclared parent-state writes
loud-fail at registration time (replay-first stance, AC-3.3).

This module asserts:

* ``declared_output_keys`` is the frozenset of ``state_schema`` field
  names (computed by :meth:`Skill._validate_declared_outputs`).
* A key absent from ``declared_output_keys`` is rejected as undeclared
  (smoke-tested via membership: the public gate documented in
  :mod:`stargraph.skills.base`).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from stargraph.skills.base import Skill, SkillKind

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


class _TwoFieldState(BaseModel):
    """State schema declaring exactly two output channels: ``a`` and ``b``."""

    a: str = ""
    b: int = 0


def test_undeclared_output_keys_rejected() -> None:
    """``declared_output_keys`` is the gate documented in ``stargraph.skills.base``."""
    skill = Skill(
        name="two-field",
        version="0.1.0",
        kind=SkillKind.utility,
        description="Skill exposing exactly two declared output channels.",
        state_schema=_TwoFieldState,
    )

    assert skill.declared_output_keys == frozenset({"a", "b"})

    # Declared keys are admitted by the whitelist.
    for declared in ("a", "b"):
        assert declared in skill.declared_output_keys

    # Undeclared keys (e.g. ``c``) must be rejected by the
    # whitelist gate -- engine SubGraphNode consumes this set as
    # the FR-23 boundary-translation check.
    output: dict[str, object] = {"a": "hi", "c": "leak"}
    undeclared = set(output.keys()) - skill.declared_output_keys
    assert undeclared == {"c"}, (
        "key 'c' must surface as undeclared via Skill.declared_output_keys whitelist diff"
    )
