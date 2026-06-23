# SPDX-License-Identifier: Apache-2.0
"""stargraph.skills.triage — the ``triage`` rule-driven reference skill.

An incoming item (subject + body + signals) → a category, route, and priority
decided by a bundled CLIPS rule pack (no LLM). See :data:`TRIAGE` for the Skill
manifest and :class:`TriageState` for the declared output channels.
"""

from __future__ import annotations

from stargraph.skills.triage._skill import TRIAGE
from stargraph.skills.triage.state import TriageState

__all__ = ["TRIAGE", "TriageState"]
