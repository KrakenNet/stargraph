# SPDX-License-Identifier: Apache-2.0
"""The ``triage`` Skill instance — a rule-driven ``workflow`` reference skill.

``workflow`` = a deterministic, multi-step orchestration. ``triage`` is the
legit generalization of a hand-wired router: it classifies an incoming item and
picks a route entirely from a bundled CLIPS rule pack (NO LLM). ``state_schema``
is the whole contract — the engine ``SubGraphNode`` lets this skill write only
``category`` / ``route`` / ``priority`` / ``matched_rules`` back to the parent
state, and ``TriageState`` declares exactly those channels.
"""

from __future__ import annotations

from stargraph.skills.base import Skill, SkillKind
from stargraph.skills.triage.state import TriageState

TRIAGE = Skill(
    name="triage",
    version="0.1.0",
    kind=SkillKind.workflow,
    description="Classify an incoming item and pick a route using a CLIPS rule pack (no LLM).",
    state_schema=TriageState,
    subgraph="stargraph.skills.triage:graph.yaml",
    requires=["rules.evaluate"],
    system_prompt="Triage the item by its signals and keywords; rules decide the route.",
)
